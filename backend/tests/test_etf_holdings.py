"""ETF 구성종목: 응답 파싱(실제 응답을 저장한 fixture), 비중 합계 검증, stale 판정, 채팅 템플릿, 수집 흐름(가짜 HTTP), API(mock). DB 없음.

실제 DB 대조는 test_etf_holdings_devdb.py. fixture는 2026-10-29에 운용사 홈페이지에서 받은 응답이다
(KODEX 200 / KODEX 레버리지 / KODEX 인버스 / TIGER 미국나스닥100).
"""

import json
import types
from datetime import date, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.api.etf as etf_api
from app import etf_holdings as E
from app import etf_holdings_collect as C
from app.disclaimer import DISCLAIMER
from app.etf import constituents_answer
from app.main import app
from evals.metrics import forbidden_hits

client = TestClient(app, raise_server_exceptions=False)
FIX = Path(__file__).parent / "fixtures"


def load_json(name):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def load_text(name):
    return (FIX / name).read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def kodex200():
    return E.parse_kodex_pdf(load_json("etf_kodex200_pdf.json"))


@pytest.fixture(scope="module")
def tiger():
    return E.parse_tiger_pdf(date(2026, 10, 28), load_text("etf_tiger_nasdaq100_pdf_list.html"))


# ---------- 파싱 ----------


class TestKodexParsing:
    def test_kodex200(self, kodex200):
        assert kodex200.source == E.KODEX and kodex200.as_of == date(2026, 10, 28)
        assert kodex200.raw_rows == 202 and len(kodex200.holdings) == 201
        assert kodex200.skipped_cash == 1 and kodex200.skipped_no_weight == 0  # 원화예금
        top = kodex200.holdings[:3]
        assert [(h.name, h.code, h.weight_pct) for h in top] == [
            ("삼성전자", "005930", 34.76), ("SK하이닉스", "000660", 25.58), ("SK스퀘어", "402340", 2.55)]
        assert E.total_weight(kodex200.holdings) == 100.0

    def test_sorted_by_weight_desc(self, kodex200):
        weights = [h.weight_pct for h in kodex200.holdings]
        assert weights == sorted(weights, reverse=True)

    def test_alphanumeric_codes_are_kept(self, kodex200):
        assert {"0126Z0", "0220W0"} <= {h.code for h in kodex200.holdings}  # 영문이 섞인 신규 종목코드

    def test_leverage_has_futures_and_sums_to_about_200(self):
        snap = E.parse_kodex_pdf(load_json("etf_kodex_leverage_pdf.json"))
        assert snap.skipped_cash == 2 and E.total_weight(snap.holdings) == 199.92
        assert (snap.holdings[0].name, snap.holdings[0].code, snap.holdings[0].weight_pct) == ("2026-12 코스피200지수선물", "KR4A016C0004", 95.67)
        assert E.weight_warnings("KODEX 레버리지", 199.92, len(snap.holdings)) == []  # 기대값 200
        assert E.weight_warnings("KODEX 200", 199.92, len(snap.holdings))  # 일반 ETF라면 경고

    def test_inverse_has_single_short_future(self):
        snap = E.parse_kodex_pdf(load_json("etf_kodex_inverse_pdf.json"))
        assert [(h.name, h.weight_pct) for h in snap.holdings] == [("2026-12 코스피200지수선물", 100.14)]
        assert snap.skipped_cash == 2 and snap.as_of == date(2026, 10, 28)
        assert E.weight_warnings("KODEX 인버스", 100.14, 1) == []

    def test_duplicate_names_are_merged(self):
        payload = {"pdf": {"gijunYMD": "20261028", "list": [
            {"secNm": "A", "itmNo": "000001", "ratio": "1.5"}, {"secNm": "A", "itmNo": "000001", "ratio": "0.5"},
            {"secNm": "B", "itmNo": None, "ratio": "2"}, {"secNm": "B", "itmNo": "", "ratio": "3"}]}}
        snap = E.parse_kodex_pdf(payload)
        assert [(h.name, h.code, h.weight_pct) for h in snap.holdings] == [("B", None, 5.0), ("A", "000001", 2.0)]
        assert snap.merged_duplicates == 2

    @pytest.mark.parametrize("payload", [{}, {"pdf": {}}, {"pdf": {"gijunYMD": "2026", "list": []}},
                                         {"pdf": {"gijunYMD": "20261028", "list": "x"}}, {"pdf": {"gijunYMD": "abcdefgh", "list": []}},
                                         None, "html"])
    def test_format_change_is_a_parse_error_without_body(self, payload):
        with pytest.raises(E.ParseError) as e:
            E.parse_kodex_pdf(payload)
        assert "html" not in str(e.value) or payload != "html"

    def test_product_list_page(self):
        page = [{"stkTicker": "069500", "fId": "2ETF01", "fNm": "KODEX 200"}, {"stkTicker": None, "fId": "x"}]
        assert E.kodex_list_page(page) == [{"ticker": "069500", "fid": "2ETF01", "name": "KODEX 200"}]
        assert E.kodex_list_page({"list": page})[0]["fid"] == "2ETF01"
        with pytest.raises(E.ParseError):
            E.kodex_list_page({"oops": 1})


class TestTigerParsing:
    def test_as_of_from_shell(self):
        assert E.parse_tiger_as_of(load_text("etf_tiger_nasdaq100_pdf_shell.html")) == date(2026, 10, 29)

    @pytest.mark.parametrize("html", ["", "<html></html>", '<input name="fixDate" value="">', '<input name="fixDate" value="2026-10-29">',
                                      '<input name="fixDate" value="2026.13.40">'])
    def test_as_of_missing_or_malformed(self, html):
        with pytest.raises(E.ParseError):
            E.parse_tiger_as_of(html)

    def test_nasdaq100(self, tiger):
        assert tiger.source == E.TIGER and tiger.raw_rows == 102 and len(tiger.holdings) == 101 and tiger.skipped_cash == 1
        assert [(h.name, h.code, h.weight_pct) for h in tiger.holdings[:2]] == [
            ("NVIDIA Corp", "NVDA US EQUITY", 8.21), ("Apple Inc", "AAPL US EQUITY", 7.92)]
        assert E.total_weight(tiger.holdings) == 100.0 and tiger.notes == []

    def test_declared_count_mismatch_is_noted(self):
        html = '<tr data-tot-cnt="5"><td>A</td><td>가</td><td>1</td><td>2</td><td>3.0</td><td>x</td></tr>'
        snap = E.parse_tiger_pdf(date(2026, 10, 28), html)
        assert snap.holdings[0].weight_pct == 3.0 and any("다릅니다" in n for n in snap.notes)

    def test_empty_table_is_a_parse_error(self):
        with pytest.raises(E.ParseError):
            E.parse_tiger_pdf(date(2026, 10, 28), "<table></table>")

    def test_isin(self):
        assert E.tiger_isin("133690") == "KR7133690008" and E.tiger_isin("069500") == "KR7069500007"
        assert E.tiger_isin("0043B0") == "KR70043B0000"  # 영문이 섞인 코드(실제 사이트에서 데이터가 확인된 값)


class TestCashAndHelpers:
    @pytest.mark.parametrize("name, code, cash", [
        ("원화예금", "KRD010010001", True), ("설정현금액", "CASH00000001", True), ("원화현금", None, True), ("KRW Cash", None, True),
        ("삼성전자", "005930", False), ("2026-12 코스피200지수선물", "KR4A016C0004", False), ("NVIDIA Corp", "NVDA US EQUITY", False),
    ])
    def test_is_cash(self, name, code, cash):
        assert E.is_cash(name, code) is cash

    @pytest.mark.parametrize("raw, code", [("-", None), ("", None), ("  ", None), (None, None), ("005930", "005930"), (" NVDA US EQUITY ", "NVDA US EQUITY")])
    def test_clean_code(self, raw, code):
        assert E._clean_code(raw) == code

    def test_dash_codes_become_null_in_both_parsers(self):
        html = '<tr data-tot-cnt="1"><td>-</td><td>KOFR Index TRS</td><td>1</td><td>2</td><td>-0.07</td><td>x</td></tr>'
        snap = E.parse_tiger_pdf(date(2026, 10, 28), html)
        assert [(h.name, h.code, h.weight_pct) for h in snap.holdings] == [("KOFR Index TRS", None, -0.07)]
        payload = {"pdf": {"gijunYMD": "20261028", "list": [{"secNm": "옵션", "itmNo": "-", "ratio": "-0.14"}]}}
        assert E.parse_kodex_pdf(payload).holdings[0].code is None

    def test_negative_weights_are_kept_and_sorted_last(self):
        snap = E._finish(E.TIGER, date(2026, 10, 28), [("매도 콜옵션", "KR4B", -0.14), ("A", "1", 5.0), ("B", "2", 0.0)])
        assert [h.weight_pct for h in snap.holdings] == [5.0, 0.0, -0.14]

    def test_rows_without_weight_are_skipped(self):
        snap = E._finish(E.KODEX, date(2026, 10, 28), [("A", "1", None), ("B", "2", 1.0), ("C", "3", None)])
        assert len(snap.holdings) == 1 and snap.skipped_no_weight == 2

    @pytest.mark.parametrize("name, issuer", [("KODEX 200", E.KODEX), ("TIGER 미국나스닥100", E.TIGER), ("tiger 200", E.TIGER),
                                              ("RISE 200", None), ("ACE 미국나스닥100", None), ("삼성전자", None)])
    def test_issuer(self, name, issuer):
        assert E.issuer_of(name) == issuer and E.issuer_supported(name) is (issuer is not None)


# ---------- 비중 합계 검증, stale ----------


class TestValidation:
    @pytest.mark.parametrize("name, total, warn", [
        ("KODEX 200", 100.0, False), ("KODEX 200", 104.9, False), ("KODEX 200", 105.1, True), ("KODEX 200", 90.0, True),
        ("KODEX 레버리지", 199.92, False), ("KODEX 레버리지", 100.0, True), ("KODEX 200선물인버스2X", 200.0, False),
        ("KODEX 인버스", 100.14, False), ("KODEX 인버스", 200.0, True),
    ])
    def test_weight_sum(self, name, total, warn):
        w = E.weight_warnings(name, total, 10)
        assert bool(w) is warn
        if warn:
            assert w[0]["kind"] == "weight_sum" and f"{total:.2f}%" in w[0]["message"] and forbidden_hits(w[0]["message"]) == []

    def test_no_data_means_no_warning(self):
        assert E.weight_warnings("KODEX 200", None, 0) == [] and E.weight_warnings("KODEX 200", 0.0, 0) == []

    @pytest.mark.parametrize("as_of, today, stale", [
        (date(2026, 10, 29), date(2026, 10, 29), False), (date(2026, 10, 28), date(2026, 10, 29), False),
        (date(2026, 10, 27), date(2026, 10, 29), True),   # 2영업일 뒤처짐
        (date(2026, 10, 23), date(2026, 10, 26), False),  # 금요일 기준일, 월요일 조회
        (date(2026, 10, 22), date(2026, 10, 26), True),
    ])
    def test_stale(self, as_of, today, stale):
        got, notice = E.stale_info(as_of, today)
        assert got is stale and (notice is not None) is stale
        if notice:
            assert as_of.isoformat() in notice

    def test_stale_without_data(self):
        assert E.stale_info(None, date(2026, 10, 29)) == (False, None)


# ---------- 채팅 템플릿 ----------


def view(name="KODEX 200", snap=None, **kw):
    snap = snap or E.parse_kodex_pdf(load_json("etf_kodex200_pdf.json"))
    items = [{"rank": i, "name": h.name, "code": h.code, "weight_pct": h.weight_pct} for i, h in enumerate(snap.holdings[:10], 1)]
    return {"ticker": "069500", "name": name, "as_of_date": snap.as_of.isoformat(), "source": E.SOURCES[snap.source], "stale": False,
            "stale_notice": None, "holding_count": len(snap.holdings), "total_weight_pct": E.total_weight(snap.holdings),
            "data_warnings": [], "items": items, **kw}


class TestChatTemplate:
    def test_with_data(self):
        text = E.holdings_answer(view())
        assert "KODEX 200의 구성종목과 비중입니다" in text and "2026-10-28 기준" in text
        assert "삼성전자 (005930) 34.76%" in text and "10. " in text and "11. " not in text
        assert "비중 합계는 100.00%" in text and "201개 구성종목" in text and "삼성자산운용 KODEX 홈페이지의 구성종목(PDF)" in text
        assert "투자 권유가 아닙니다" in text and "운용사 홈페이지" in text
        assert "레버리지" not in text  # 일반 ETF에는 레버리지 안내가 없다

    def test_numbers_come_only_from_the_view(self):
        import re
        text = E.holdings_answer(view())
        allowed = {"34.76", "25.58", "2.55", "100.00", "005930", "2026", "10", "28", "200", "201"}
        top = E.parse_kodex_pdf(load_json("etf_kodex200_pdf.json")).holdings[:10]
        allowed |= {f"{h.weight_pct:.2f}" for h in top} | {h.code for h in top} | {f"{sum(h.weight_pct for h in top):.2f}"}
        allowed |= {str(i) for i in range(1, 11)}
        for num in re.findall(r"\d+(?:\.\d+)?", text):
            assert num in allowed or num in "".join(h.name for h in top), num

    def test_leverage_keeps_the_safety_notice(self):
        snap = E.parse_kodex_pdf(load_json("etf_kodex_leverage_pdf.json"))
        text = E.holdings_answer(view("KODEX 레버리지", snap))
        assert "레버리지 ETF입니다" in text and "복리 효과" in text and "100%와 다를 수 있습니다" in text
        assert "2026-12 코스피200지수선물 (KR4A016C0004) 95.67%" in text

    def test_inverse_notice_mentions_short_position(self):
        snap = E.parse_kodex_pdf(load_json("etf_kodex_inverse_pdf.json"))
        text = E.holdings_answer(view("KODEX 인버스", snap))
        assert "인버스 ETF입니다" in text and "매도 포지션" in text

    def test_stale_and_warning_lines(self):
        text = E.holdings_answer(view(stale=True, stale_notice="마지막으로 수집한 구성종목 기준일은 2026-10-20입니다.",
                                      data_warnings=[{"kind": "weight_sum", "message": "합계 이상"}]))
        assert "기준일은 2026-10-20입니다. 최신 내용과 다를 수 있습니다" in text and "※ 합계 이상" in text

    def test_tiger_foreign_holdings(self, tiger):
        text = E.holdings_answer(view("TIGER 미국나스닥100", tiger))
        assert "미래에셋자산운용 TIGER ETF 홈페이지" in text and "NVIDIA Corp (NVDA US EQUITY) 8.21%" in text

    @pytest.mark.parametrize("q, n", [
        ("TIGER 200 구성종목 비중 상위 5개 알려줘", 5), ("KODEX 200 상위 3개 종목 알려줘", 3), ("KODEX 200 구성종목 TOP3", 3),
        ("KODEX 200 구성종목 top 7 알려줘", 7), ("TIGER 미국S&P500 보유종목 TOP10은?", 10), ("KODEX 200 구성종목 비중 상위 20개", 20),
        ("TIGER 반도체TOP10 구성종목이랑 비중 알려줘", None),  # ETF 이름 속 TOP10은 개수 요청이 아니다
        ("KODEX 200 구성종목 알려줘", None), ("KODEX 200 상위 0개", None), ("TIGER 200 구성종목 알려줘", None),
    ])
    def test_requested_top_n(self, q, n):
        assert E.requested_top_n(q) == n

    def test_top_n_limits_the_list_and_the_sum_line(self):
        text = E.holdings_answer(view(), top_n=3)
        assert "3. SK스퀘어 (402340) 2.55%" in text and "4. " not in text and "위 3개 비중 합계는 62.89%" in text

    def test_top_n_above_ten_is_capped_and_says_so(self):
        text = E.holdings_answer(view(), top_n=20)
        assert "10. " in text and "11. " not in text and "한 번에 상위 10개까지만 보여드립니다" in text

    def test_missing_etf_keeps_the_existing_refusal(self):
        text = E.constituents_chat_answer([], ["ACE 미국나스닥100"], constituents_answer)
        assert text == constituents_answer(["ACE 미국나스닥100"]) and "제공하지 않습니다" in text

    def test_mixed_data_and_missing(self):
        text = E.constituents_chat_answer([view()], ["ACE 미국나스닥100"], constituents_answer)
        assert "삼성전자 (005930)" in text and "ACE 미국나스닥100의 구성종목과 편입 비중 데이터는 이 서비스에서 제공하지 않습니다" in text

    def test_at_most_three_etfs(self):
        text = E.constituents_chat_answer([view(f"KODEX {i}") for i in range(5)], [], constituents_answer)
        assert text.count("의 구성종목과 비중입니다") == 3 and "KODEX 3, KODEX 4의 구성종목은" in text

    def test_no_forbidden_expressions_anywhere(self, tiger):
        texts = [E.holdings_answer(view()), E.holdings_answer(view("TIGER 미국나스닥100", tiger)),
                 E.holdings_answer(view("KODEX 레버리지", E.parse_kodex_pdf(load_json("etf_kodex_leverage_pdf.json")))),
                 E.holdings_answer(view("KODEX 인버스", E.parse_kodex_pdf(load_json("etf_kodex_inverse_pdf.json")))),
                 E.holdings_answer(view(stale=True, stale_notice="마지막으로 수집한 구성종목 기준일은 2026-10-20입니다.")),
                 E.MSG_NO_DATA, E.MSG_NOT_COLLECTED, E.not_etf_message("삼성전자"), E.FRESHNESS_NOTE, DISCLAIMER,
                 *[w["message"] for w in E.weight_warnings("KODEX 200", 130.0, 5)]]
        assert len(texts) >= 10
        for t in texts:
            assert forbidden_hits(t) == [], t


# ---------- 수집 흐름(가짜 HTTP) ----------


class FakeResp:
    def __init__(self, payload=None, text=""):
        self._payload, self.text = payload, text

    def json(self):
        return self._payload


class FakeSite:
    """URL로 응답을 고르는 가짜 get. 호출 기록과 URL별 실패를 주입할 수 있다."""

    def __init__(self):
        self.calls, self.fail = [], {}
        self.kodex_page = [{"stkTicker": "069500", "fId": "2ETF01", "fNm": "KODEX 200"},
                           {"stkTicker": "122630", "fId": "2ETF25", "fNm": "KODEX 레버리지"}]

    def __call__(self, url, params=None, headers=None, timeout=None, label="", **kw):
        self.calls.append((url, dict(params or {}), headers))
        for needle, exc in self.fail.items():
            if needle in url or needle in str(params):
                raise exc
        if url.endswith("/product.do"):
            return FakeResp(self.kodex_page if params["pageNo"] == 1 else [])
        if "product-pdf/2ETF01" in url:
            return FakeResp(load_json("etf_kodex200_pdf.json"))
        if "product-pdf/2ETF25" in url:
            return FakeResp(load_json("etf_kodex_leverage_pdf.json"))
        if url.endswith("/pdf.ajax"):
            return FakeResp(text=load_text("etf_tiger_nasdaq100_pdf_shell.html"))
        if url.endswith("/pdfListAjax.ajax"):
            return FakeResp(text=load_text("etf_tiger_nasdaq100_pdf_list.html"))
        raise AssertionError(url)


class FakeDB:
    """collect()가 쓰는 db.execute(select ETF 목록)와 save_snapshot 대체용 rollback만 흉내낸다."""

    def __init__(self, etfs):
        self.etfs, self.rollbacks = etfs, 0

    def execute(self, stmt):
        class R:
            def all(_):
                return self.etfs
        return R()

    def rollback(self):
        self.rollbacks += 1


class Row:
    def __init__(self, id, ticker, name):
        self.id, self.ticker, self.name = id, ticker, name


ETFS = [Row(1, "069500", "KODEX 200"), Row(2, "122630", "KODEX 레버리지"), Row(3, "133690", "TIGER 미국나스닥100"),
        Row(4, "148020", "RISE 200")]


@pytest.fixture
def saved(monkeypatch):
    out = []
    monkeypatch.setattr(C, "save_snapshot", lambda db, company_id, snap, *a: out.append((company_id, snap.as_of, len(snap.holdings))))
    return out


def run(site, **kw):
    sleeps = []
    client_ = C.PoliteClient(2.0, get=site, sleep=sleeps.append, clock=lambda: 0.0)
    return C.collect(FakeDB(ETFS), client=client_, today=date(2026, 10, 29), log=lambda m: None, retry_pause=30.0, fid_cache=kw.pop("fid_cache", None), **kw), sleeps, client_


class TestCollect:
    def test_default_delay_is_three_seconds(self):
        assert C.DEFAULT_DELAY_S == 3.0 and C.RETRY_PAUSE_S == 30.0

    def test_collects_supported_etfs_and_skips_others(self, saved):
        site = FakeSite()
        results, _, _ = run(site)
        assert [(r.ticker, r.ok, r.skipped is not None) for r in results] == [
            ("069500", True, False), ("122630", True, False), ("133690", True, False), ("148020", False, True)]
        assert saved == [(1, date(2026, 10, 28), 201), (2, date(2026, 10, 28), 208), (3, date(2026, 10, 29), 101)]
        assert results[0].total_weight_pct == 100.0 and results[1].total_weight_pct == 199.92 and results[1].warnings == []

    def test_requests_are_identified_and_throttled(self, saved):
        site = FakeSite()
        results, sleeps, client_ = run(site)
        assert all(h == {"User-Agent": C.USER_AGENT} for _, _, h in site.calls) and "research-demo" in C.USER_AGENT
        # 상품 목록 1페이지(찾는 종목이 다 나와 거기서 멈춤) + KODEX 구성종목 2 + TIGER 기준일·표 2 = 5건.
        # 첫 요청 뒤 모든 요청 앞에서 쉰다(가짜 시계가 멈춰 있어 매번 delay만큼)
        assert client_.requests == len(site.calls) == 5 and len(sleeps) == 4 and set(sleeps) == {2.0}

    def test_date_parameters(self, saved):
        site = FakeSite()
        run(site)
        pdf = next(p for u, p, _ in site.calls if "product-pdf/2ETF01" in u)
        assert pdf == {"gijunYMD": "2026.10.29"}
        listing = next(p for u, p, _ in site.calls if u.endswith("/pdfListAjax.ajax"))
        assert listing["ksdFund"] == "KR7133690008" and listing["fixDate"] == "2026.10.29" and listing["listCnt"] == 2000

    def test_one_failure_does_not_stop_the_others(self, saved):
        import requests
        site = FakeSite()
        site.fail = {"product-pdf/2ETF01": requests.HTTPError("403 Forbidden for url: https://www.samsungfund.com/x")}
        results, _, _ = run(site)
        assert [r.ok for r in results[:3]] == [False, True, True]
        assert "HTTPError" in results[0].error and [s[0] for s in saved] == [2, 3]

    def test_format_change_is_reported_per_etf(self, saved):
        site = FakeSite()
        site.kodex_page = [{"stkTicker": "069500", "fId": "2ETF01"}, {"stkTicker": "122630", "fId": "2ETF25"}]
        orig = site.__call__

        def broken(url, **kw):
            if "pdfListAjax" in url:
                return FakeResp(text="<html>점검 중</html>")
            return orig(url, **kw)

        results, _, _ = run(broken)
        assert [r.ok for r in results[:3]] == [True, True, False] and "ParseError" in results[2].error

    def test_kodex_list_failure_marks_only_kodex(self, saved):
        import requests
        site = FakeSite()
        site.fail = {"/product.do": requests.ConnectionError("down")}
        results, _, _ = run(site)
        assert [r.ok for r in results[:3]] == [False, False, True] and "상품 목록 조회 실패" in results[0].error

    def test_ticker_missing_from_kodex_list(self, saved):
        site = FakeSite()
        site.kodex_page = [{"stkTicker": "069500", "fId": "2ETF01"}]
        results, _, _ = run(site)
        assert results[1].ok is False and "찾지 못했습니다" in results[1].error and results[0].ok

    def test_transient_failure_is_retried_once_after_a_pause(self, saved):
        from app.http_retry import TransientHTTPError
        site = FakeSite()
        state = {"n": 0}
        orig = site.__call__

        def flaky(url, **kw):
            if "product-pdf/2ETF01" in url:
                state["n"] += 1
                if state["n"] <= 2:  # 클라이언트의 자체 재시도(1회)까지 실패해야 2차 패스로 넘어간다
                    raise TransientHTTPError("HTTP 503", response=types.SimpleNamespace(status_code=503))
            return orig(url, **kw)

        results, sleeps, _ = run(flaky)
        assert results[0].ok is True and results[0].error is None and state["n"] == 3
        assert 30.0 in sleeps and sorted(s[0] for s in saved) == [1, 2, 3]

    def test_transient_failure_that_persists_stays_failed(self, saved):
        from app.http_retry import TransientHTTPError
        site = FakeSite()
        site.fail = {"product-pdf/2ETF01": TransientHTTPError("HTTP 503", response=types.SimpleNamespace(status_code=503))}
        results, sleeps, _ = run(site)
        assert results[0].ok is False and "TransientHTTPError" in results[0].error and sleeps.count(30.0) == 1

    def test_non_transient_failure_is_not_retried(self, saved):
        import requests
        site = FakeSite()
        site.fail = {"product-pdf/2ETF01": requests.HTTPError("404")}
        results, sleeps, _ = run(site)
        assert results[0].ok is False and 30.0 not in sleeps

    def _blocked(self, status=429):
        return types.SimpleNamespace(status_code=status)

    def test_429_blocks_the_host_and_sends_no_more_requests(self, saved):
        """삼성이 429(봇 확인 화면)를 주면 KODEX 전체를 그 자리에서 멈추고 더 요청하지 않는다. 다른 호스트(TIGER)는 계속한다."""
        from app.http_retry import TransientHTTPError
        site = FakeSite()
        site.fail = {"samsungfund.com": TransientHTTPError("HTTP 429", response=self._blocked(429))}
        results, sleeps, client_ = run(site)
        samsung_calls = [u for u, _, _ in site.calls if "samsungfund.com" in u]
        assert len(samsung_calls) == 1  # 목록 한 번 요청했다가 막혀서 멈춤(재시도도 없음)
        assert [r.ok for r in results[:3]] == [False, False, True]
        assert "HostBlocked" in results[0].error and "우회하지 않고" in results[0].error and "samsungfund.com" in results[0].error
        assert results[0].retryable is False and results[1].retryable is False and 30.0 not in sleeps
        assert [s[0] for s in saved] == [3] and list(client_.blocked) == ["www.samsungfund.com"]

    def test_403_blocks_too_and_later_calls_never_leave_the_process(self, saved):
        from app.http_retry import TransientHTTPError
        calls = []

        def get(url, **kw):
            calls.append(url)
            raise TransientHTTPError("HTTP 403", response=self._blocked(403))

        c = C.PoliteClient(2.0, get=get, sleep=lambda s: None, clock=lambda: 0.0)
        for _ in range(3):
            with pytest.raises(C.HostBlocked):
                c.get("https://example.test/a")
        assert len(calls) == 1 and c.requests == 1

    @pytest.mark.parametrize("resp, why", [
        (types.SimpleNamespace(status_code=200, text="<html><title>Just a moment...</title>", headers={}), "본문"),
        (types.SimpleNamespace(status_code=503, text="x", headers={"cf-mitigated": "challenge"}), "헤더"),
    ])
    def test_a_challenge_page_with_200_or_503_also_blocks_the_host(self, resp, why):
        calls = []

        def get(url, **kw):
            calls.append(url)
            return resp

        c = C.PoliteClient(2.0, get=get, sleep=lambda s: None, clock=lambda: 0.0)
        with pytest.raises(C.HostBlocked) as e:
            c.get("https://example.test/a")
        assert "우회하지 않고" in str(e.value)
        with pytest.raises(C.HostBlocked):
            c.get("https://example.test/b")
        assert len(calls) == 1

    def test_normal_html_with_rows_is_not_a_challenge(self):
        c = C.PoliteClient(2.0, get=lambda *a, **k: FakeResp(text=load_text("etf_tiger_nasdaq100_pdf_list.html")), sleep=lambda s: None)
        assert c.get("https://example.test/a").text.startswith("<tr")

    def test_5xx_is_retried_once_by_the_client_but_never_with_http_retry_backoff(self):
        from app.http_retry import TransientHTTPError
        calls, waits = [], []

        def get(url, **kw):
            calls.append(kw.get("retries"))
            if len(calls) == 1:
                raise TransientHTTPError("HTTP 503", response=self._blocked(503))
            return FakeResp()

        c = C.PoliteClient(2.0, get=get, sleep=waits.append, clock=lambda: 0.0)
        assert isinstance(c.get("https://example.test/a"), FakeResp)
        assert calls == [0, 0] and waits == [2.0]  # http_retry의 자체 재시도는 끄고(retries=0) 이 클래스가 한 번만 다시 시도

    def test_fid_cache_avoids_the_product_list(self, tmp_path, saved):
        cache = tmp_path / "fid.json"
        cache.write_text(json.dumps({"069500": "2ETF01", "122630": "2ETF25", "999999": "X"}), encoding="utf-8")
        site = FakeSite()
        results, _, _ = run(site, fid_cache=cache)
        assert not any(u.endswith("/product.do") for u, _, _ in site.calls)  # 목록을 한 번도 받지 않았다
        assert [r.ok for r in results[:3]] == [True, True, True]

    def test_fid_cache_is_filled_from_the_list_and_reused(self, tmp_path, saved):
        cache = tmp_path / "fid.json"
        site = FakeSite()
        run(site, fid_cache=cache)
        assert json.loads(cache.read_text(encoding="utf-8")) == {"069500": "2ETF01", "122630": "2ETF25"}
        site2 = FakeSite()
        run(site2, fid_cache=cache)
        assert sum(u.endswith("/product.do") for u, _, _ in site.calls) == 1
        assert sum(u.endswith("/product.do") for u, _, _ in site2.calls) == 0

    def test_corrupt_cache_is_ignored(self, tmp_path, saved):
        cache = tmp_path / "fid.json"
        cache.write_text("{not json", encoding="utf-8")
        results, _, _ = run(FakeSite(), fid_cache=cache)
        assert results[0].ok and json.loads(cache.read_text(encoding="utf-8"))["069500"] == "2ETF01"

    def test_dry_run_saves_nothing(self, saved):
        results, _, _ = run(FakeSite(), dry_run=True)
        assert saved == [] and [r.ok for r in results[:3]] == [True, True, True]

    def test_empty_snapshot_is_a_failure_not_an_empty_save(self, saved):
        site = FakeSite()
        site.kodex_page = [{"stkTicker": "069500", "fId": "2ETF01"}]
        orig = site.__call__

        def empty(url, **kw):
            if "product-pdf/2ETF01" in url:
                return FakeResp({"pdf": {"gijunYMD": "20261028", "list": [{"secNm": "원화예금", "itmNo": "KRD1", "ratio": None}]}})
            return orig(url, **kw)

        results, _, _ = run(empty, tickers=None)
        assert results[0].ok is False and "0개" in results[0].error and 1 not in [s[0] for s in saved]

    def test_politeness_helper_waits_only_the_remainder(self):
        now = [0.0]
        waits = []
        c = C.PoliteClient(1.5, get=lambda *a, **k: FakeResp(), sleep=waits.append, clock=lambda: now[0])
        c.get("u")
        now[0] = 1.0
        c.get("u")
        now[0] = 10.0
        c.get("u")
        assert waits == [0.5]


# ---------- API(mock) ----------


class FakeCompany:
    def __init__(self, ticker, name, asset_type):
        self.id, self.ticker, self.name, self.asset_type = 1, ticker, name, asset_type


class Res:
    def __init__(self, company=None, message=None):
        self.company, self.message = company, message


@pytest.fixture
def api(monkeypatch):
    companies = {"069500": FakeCompany("069500", "KODEX 200", "etf"), "005930": FakeCompany("005930", "삼성전자", "stock"),
                 "148020": FakeCompany("148020", "RISE 200", "etf"), "133690": FakeCompany("133690", "TIGER 미국나스닥100", "etf")}
    monkeypatch.setattr(etf_api, "SessionLocal", lambda: type("S", (), {"close": lambda self: None})())
    monkeypatch.setattr(etf_api, "resolve_company",
                        lambda db, raw: Res(companies[raw]) if raw in companies else Res(message="정확한 종목을 찾지 못했습니다. 혹시 KODEX 200인가요?"))
    seen = []

    def fake_view(db, company, limit, today=None):
        seen.append((company.ticker, limit))
        if company.ticker != "069500":
            return {"ticker": company.ticker, "name": company.name, "asset_type": "etf", "limit": limit, "disclaimer": DISCLAIMER,
                    "as_of_date": None, "collected_at": None, "stale": False, "stale_notice": None, "source": None, "holding_count": 0,
                    "total_weight_pct": None, "top_weight_pct": None, "items": [], "data_warnings": [],
                    "message": E.MSG_NO_DATA if not E.issuer_supported(company.name) else E.MSG_NOT_COLLECTED}
        return {**view(), "asset_type": "etf", "limit": limit, "disclaimer": DISCLAIMER, "collected_at": "2026-10-29T16:20:00+09:00",
                "top_weight_pct": 99.0, "message": None, "items": view()["items"][:limit]}

    monkeypatch.setattr(etf_api, "holdings_view", fake_view)
    return seen


FIELDS = {"ticker", "name", "asset_type", "as_of_date", "collected_at", "stale", "stale_notice", "source", "holding_count",
          "total_weight_pct", "top_weight_pct", "limit", "items", "data_warnings", "message", "disclaimer"}


class TestApi:
    def test_public_no_login_needed_and_contract(self, api):
        r = client.get("/api/etf/069500/holdings")
        assert r.status_code == 200
        body = r.json()
        assert set(body) == FIELDS and body["limit"] == 10 and len(body["items"]) == 10
        assert set(body["items"][0]) == {"rank", "name", "code", "weight_pct"} and body["items"][0]["rank"] == 1
        assert body["as_of_date"] == "2026-10-28" and body["total_weight_pct"] == 100.0 and body["message"] is None
        assert body["disclaimer"] == DISCLAIMER

    def test_limit(self, api):
        assert len(client.get("/api/etf/069500/holdings?limit=3").json()["items"]) == 3
        assert client.get("/api/etf/069500/holdings?limit=50").status_code == 200
        assert api[-1] == ("069500", 50)

    @pytest.mark.parametrize("q", ["limit=0", "limit=51", "limit=-1", "limit=abc", "limit="])
    def test_limit_out_of_range_is_422(self, api, q):
        assert client.get(f"/api/etf/069500/holdings?{q}").status_code == 422
        assert api == []

    def test_unknown_ticker_is_404_with_candidates(self, api):
        r = client.get("/api/etf/KODEX200/holdings")
        assert r.status_code == 404 and "혹시 KODEX 200" in r.json()["detail"]

    def test_stock_is_404_not_an_etf(self, api):
        r = client.get("/api/etf/005930/holdings")
        assert r.status_code == 404 and r.json()["detail"] == "삼성전자는 ETF가 아니라 구성종목 비중을 제공하지 않습니다." and api == []
        assert E.not_etf_message("SK하이닉스") == "SK하이닉스는 ETF가 아니라 구성종목 비중을 제공하지 않습니다."
        assert E.not_etf_message("현대모비스") == "현대모비스는 ETF가 아니라 구성종목 비중을 제공하지 않습니다."
        assert E.not_etf_message("셀트리온") == "셀트리온은 ETF가 아니라 구성종목 비중을 제공하지 않습니다."

    def test_etf_without_data_is_200_with_message(self, api):
        for ticker, msg in (("148020", E.MSG_NO_DATA), ("133690", E.MSG_NOT_COLLECTED)):
            r = client.get(f"/api/etf/{ticker}/holdings")
            body = r.json()
            assert r.status_code == 200 and body["items"] == [] and body["as_of_date"] is None and body["message"] == msg
            assert set(body) == FIELDS and body["holding_count"] == 0
            assert forbidden_hits(body["message"]) == []


# ---------- 채팅 연결(agent) ----------


class Co:
    def __init__(self, name, asset_type="etf"):
        self.id, self.ticker, self.name, self.asset_type = 1, "069500", name, asset_type


@pytest.fixture
def agent_db(monkeypatch):
    import app.agent as agent

    monkeypatch.setattr(agent, "SessionLocal", lambda: type("S", (), {"close": lambda self: None})())
    return agent


class TestAgentWiring:
    def test_etf_with_data_gets_the_template_without_llm(self, agent_db, monkeypatch):
        monkeypatch.setattr(agent_db, "chat_views", lambda db, cs: ([view()], []))
        resp, records = agent_db._answer_etf_constituents("KODEX 200 구성종목 비중 알려줘", [Co("KODEX 200")])
        assert "삼성전자 (005930) 34.76%" in resp["answer"] and "기준" in resp["answer"]
        assert records == [] and resp["used_tools"] == []
        resp, _ = agent_db._answer_etf_constituents("KODEX 200 구성종목 비중 상위 2개 알려줘", [Co("KODEX 200")])
        assert "2. SK하이닉스" in resp["answer"] and "3. " not in resp["answer"]

    def test_etf_without_data_keeps_the_refusal(self, agent_db, monkeypatch):
        monkeypatch.setattr(agent_db, "chat_views", lambda db, cs: ([], ["ACE 미국나스닥100"]))
        resp, _ = agent_db._answer_etf_constituents("ACE 미국나스닥100 구성종목 알려줘", [Co("ACE 미국나스닥100")])
        assert resp["answer"] == constituents_answer(["ACE 미국나스닥100"])

    def test_one_with_data_one_without(self, agent_db, monkeypatch):
        monkeypatch.setattr(agent_db, "chat_views", lambda db, cs: ([view()], ["ACE 미국나스닥100"]))
        resp, _ = agent_db._answer_etf_constituents("KODEX 200이랑 ACE 미국나스닥100 구성종목", [Co("KODEX 200"), Co("ACE 미국나스닥100")])
        assert "삼성전자 (005930)" in resp["answer"] and "ACE 미국나스닥100의 구성종목과 편입 비중 데이터는 이 서비스에서 제공하지 않습니다" in resp["answer"]

    def test_db_error_falls_back_to_the_refusal(self, agent_db, monkeypatch):
        def boom(db, cs):
            raise RuntimeError("db down")

        monkeypatch.setattr(agent_db, "chat_views", boom)
        resp, _ = agent_db._answer_etf_constituents("KODEX 200 구성종목", [Co("KODEX 200")])
        assert resp["answer"] == constituents_answer(["KODEX 200"])

    def test_not_applicable_questions_are_untouched(self, agent_db, monkeypatch):
        monkeypatch.setattr(agent_db, "chat_views", lambda db, cs: pytest.fail("조회하면 안 됨"))
        assert agent_db._answer_etf_constituents("삼성전자 구성종목", [Co("삼성전자", "stock")]) is None
        assert agent_db._answer_etf_constituents("KODEX 200 주가", [Co("KODEX 200")]) is None
        assert agent_db._answer_etf_constituents("구성종목이 뭐야", None) is None
