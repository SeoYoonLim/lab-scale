import io
import zipfile

import pytest
import requests

import app.dart_documents as dd
from app.dart_documents import CONTENT_MAX_LEN, FetchOutcome, QuotaExceeded, extract_text, fetch_document_text

DART_XML = (
    '<?xml version="1.0" encoding="utf-8"?>\r\n<DOCUMENT xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">\n'
    '<DOCUMENT-NAME ACODE="10800">증권발행실적보고서</DOCUMENT-NAME>\n<FORMULA-VERSION SUBVER="1">6.0</FORMULA-VERSION>\n'
    "<COMPANY-NAME>주식회사 대한항공</COMPANY-NAME>\n<BODY><P>총 발행금액 : 350,000,000,000</P></BODY></DOCUMENT>"
)
HTML_FORM = (
    '<html><head><meta content="text/html; charset=euc-kr"><STYLE>.x{font:돋움체}</STYLE></head>'
    "<body><p>기업설명회(IR) 개최(안내공시)</p><p>1. 일시 및 장소&nbsp;2026-07-30</p><!-- c --></body></html>"
)


def make_zip(raw: bytes, name="20260916000407.xml") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(name, raw)
    return buf.getvalue()


class FakeResp:
    def __init__(self, content=b"", exc=None):
        self.content = content
        self._exc = exc

    def raise_for_status(self):
        if self._exc:
            raise self._exc


class FakeHttp:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = 0

    def get(self, *a, **kw):
        self.calls += 1
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("DART_API_KEY", "x" * 40)
    monkeypatch.setattr(dd.time, "sleep", lambda s: None)


class TestExtractText:
    def test_dart_xml_strips_tags_and_leading_title_and_version(self):
        text = extract_text(DART_XML.encode(), "증권발행실적보고서")
        assert text == "주식회사 대한항공 총 발행금액 : 350,000,000,000"

    def test_html_form_drops_style_head_comment_and_entities(self):
        text = extract_text(HTML_FORM.encode(), "기업설명회(IR)개최(안내공시)")
        assert text == "1. 일시 및 장소 2026-07-30"

    def test_title_differing_from_document_name_is_kept(self):
        text = extract_text(DART_XML.encode(), "완전히 다른 제목")
        assert text.startswith("증권발행실적보고서 6.0")

    def test_truncated_to_max_len(self):
        raw = ("<P>" + "가" * 5000 + "</P>").encode()
        assert len(extract_text(raw)) == CONTENT_MAX_LEN

    def test_euc_kr_bytes_decoded(self):
        assert extract_text("<P>삼성전자</P>".encode("euc-kr")) == "삼성전자"

    def test_empty_document(self):
        assert extract_text(b"<html><head></head><body> </body></html>") == ""


class TestFetchDocumentText:
    def test_success(self):
        http = FakeHttp(FakeResp(make_zip(DART_XML.encode())))
        out = fetch_document_text("20260916000407", "증권발행실적보고서", http)
        assert out.status == "ok" and out.text.startswith("주식회사 대한항공")

    @pytest.mark.parametrize("status", ["013", "014"])
    def test_no_file_is_permanent_failure(self, status):
        body = f"<result><status>{status}</status><message>x</message></result>".encode()
        out = fetch_document_text("1", "", FakeHttp(FakeResp(body)))
        assert out.status == "no_file" and out.detail == status

    def test_quota_exceeded_raises(self):
        body = b"<result><status>020</status><message>x</message></result>"
        with pytest.raises(QuotaExceeded):
            fetch_document_text("1", "", FakeHttp(FakeResp(body)))

    def test_unknown_status_is_transient(self):
        body = b"<result><status>800</status></result>"
        assert fetch_document_text("1", "", FakeHttp(FakeResp(body))).status == "transient"

    def test_network_error_retried_then_transient(self):
        http = FakeHttp(*[requests.ConnectionError()] * 3)
        out = fetch_document_text("1", "", http)
        assert out.status == "transient" and http.calls == 3

    def test_network_error_then_success(self):
        http = FakeHttp(requests.Timeout(), FakeResp(make_zip(DART_XML.encode())))
        assert fetch_document_text("1", "", http).status == "ok"

    def test_zip_without_text_is_empty(self):
        out = fetch_document_text("1", "", FakeHttp(FakeResp(make_zip(b"<html><body></body></html>"))))
        assert out.status == "empty"

    def test_empty_zip(self):
        buf = io.BytesIO()
        zipfile.ZipFile(buf, "w").close()
        assert fetch_document_text("1", "", FakeHttp(FakeResp(buf.getvalue()))).status == "empty"

    def test_huge_document_read_is_capped(self, monkeypatch):
        monkeypatch.setattr(dd, "READ_LIMIT", 1000)
        raw = ("<P>" + "가나다 " * 100000 + "</P>").encode()
        out = fetch_document_text("1", "", FakeHttp(FakeResp(make_zip(raw))))
        assert out.status == "ok" and len(out.text) < 1000


class TestStripBoilerplate:
    COVER = (
        "금융위원회 한국거래소 귀중 2026년 8월 14일 회 사 명 : 삼성전자주식회사 대 표 이 사 : 전 영 현 "
        "본 점 소 재 지 : 경기도 수원시 (전 화) 031-200-1114 (홈페이지) http://www.samsung.com/sec "
        "작 성 책 임 자 : (직 책) 재경팀장 (성 명) 김동욱 (전 화) 031-277-7218 자기주식 취득 결정 1. 취득예정주식(주) 53,285,968"
    )

    def test_cover_block_removed_and_body_kept(self):
        out = dd.strip_boilerplate(self.COVER)
        assert out == "2026년 8월 14일 자기주식 취득 결정 1. 취득예정주식(주) 53,285,968"

    def test_contact_line_of_form_removed(self):
        out = dd.strip_boilerplate("담당자명 정재훈 tel 02-2255-9000 (fax) 02-2255-6164 2. 발행주식수 정보")
        assert out == "담당자명 정재훈 2. 발행주식수 정보"

    def test_text_without_boilerplate_unchanged(self):
        s = "1. 일시 및 장소 일시 2026-07-30 10:00 장소 -"
        assert dd.strip_boilerplate(s) == s

    def test_company_block_without_phone_does_not_swallow_body(self):
        body = "회 사 명 : 가나다 " + "본문내용 " * 200 + "(전 화) 02-123-4567"
        assert "본문내용" in dd.strip_boilerplate(body)

    def test_extract_text_applies_it(self):
        raw = f"<html><body><p>{self.COVER}</p></body></html>".encode()
        assert extract_text(raw).startswith("2026년 8월 14일 자기주식 취득 결정")
