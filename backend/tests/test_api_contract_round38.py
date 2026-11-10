"""38차(2026-11-09): 번역 상태·시간대·locale fallback·종목 매칭 계약(단위 테스트, DB·외부 HTTP·LLM 없음).

- POST /api/research에 locale(실제로 쓴 언어)·translation_status(ko-KR이면 null)를 하위 호환으로 더했다.
- 번역은 i18n_translate._chat을 가짜로 바꿔 재현한다(성공·연결 실패·검증 실패·일부 청크 실패).
- 시각 필드는 바꾸지 않았다: 저장 시각은 UTC(+00:00), 새로 계산한 시각은 KST(+09:00), 둘 다 오프셋이 있다는 것을 고정한다.
- 번역 실패 요약·코드 요약의 뉴스·공시 날짜가 UTC 앞 10자로 하루 이르게 나오던 문제를 고쳤다(app.display_dates.kst_day).
"""

import re
import typing
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import TIMESTAMP

import app.agent as agent
import app.api.research as research_api
import app.i18n_translate as tr
from app import answer_i18n, history_dates, i18n, quote_i18n
from app.answer_guard import code_summary
from app.briefing import KST, _iso_kst
from app.disclaimer import DISCLAIMER
from app.display_dates import kst_day
from app.main import app
from app.outlook import kst_date
from app.tools.company_resolver import resolve_company

client = TestClient(app, raise_server_exceptions=False)
ROOT = Path(__file__).resolve().parents[2]
NEW_KEYS = {"answer", "used_tools", "sources", "report_id", "previous_report_id", "disclaimer", "locale", "translation_status"}
KOREAN = "삼성전자의 최근 흐름은 완만한 상승입니다. 자세한 근거는 공시를 확인하세요."
NEWS = {"tool_name": "news_tool", "arguments": {}, "result": {"found": True, "news": [
    {"title": "삼성전자 신제품 발표", "published_at": "2026-10-27T23:30:00+00:00"},  # KST 10-28 08:30
]}}
DISCLOSURE = {"tool_name": "disclosure_tool", "arguments": {}, "result": {"found": True, "disclosures": [
    {"title": "삼성전자 분기보고서", "disclosed_at": "2026-10-27T15:00:00+00:00"},  # KST 10-28 00:00(공시 저장 방식)
]}}
STOCK = {"tool_name": "stock_tool", "arguments": {}, "result": {"found": True, "company_name": "삼성전자", "ticker": "005930",
                                                                 "prices": [{"price_date": "2026-11-06", "close_price": 71500,
                                                                             "change_pct": -1.23, "volume": 12345678}]}}


def good_translation(messages):
    """자리표시자를 한 번씩 그대로 두고 한글·숫자 없이 쓴 번역(검증 통과)."""
    tokens = re.findall(r"⟦\d+⟧", messages[1]["content"])
    if "Simplified Chinese" in messages[0]["content"]:  # zh-CN 번역문은 한자가 있어야 검증을 통과한다
        return " ".join(tokens) + " 呈上涨趋势。"
    return " ".join(tokens) + " is trending upward."


@pytest.fixture
def pipeline(monkeypatch, login_as):
    """실제 ask_question → answer_i18n → i18n_translate 경로. _answer(LLM)·저장·DB 종목명만 가짜."""
    login_as(7)
    saved = []
    state = {"answer": KOREAN, "records": []}
    monkeypatch.setattr(agent, "_answer", lambda q, model, previous=None: (
        {"answer": state["answer"], "used_tools": [r["tool_name"] for r in state["records"]], "sources": []}, state["records"]))
    monkeypatch.setattr(agent, "save_report_row", lambda question, answer, records, previous_report_id=None, user_id=None, **extra:
                        saved.append({"answer": answer, **extra}) or 41)
    monkeypatch.setattr(answer_i18n, "_company_names", lambda: ("삼성전자",))
    state["saved"] = saved
    return state


def post(body):
    r = client.post("/api/research", json=body)
    assert r.status_code == 200, r.text
    assert set(r.json()) == NEW_KEYS
    return r.json()


# ---------- 1~6, 11~13: 리서치 번역 상태 ----------


class TestResearchTranslationStatus:
    def test_korean_answer_is_unchanged_and_status_null(self, pipeline, monkeypatch):  # 1
        monkeypatch.setattr(tr, "_chat", lambda m: pytest.fail("ko-KR은 번역하지 않는다"))
        body = post({"question": "삼성전자 어때?"})
        assert body["answer"] == KOREAN and body["locale"] == "ko-KR" and body["translation_status"] is None
        assert body["disclaimer"] == DISCLAIMER
        assert pipeline["saved"] == [{"answer": KOREAN}]  # ko-KR 기록에는 locale·원문·번역 상태를 넘기지 않는다(예전과 같음)

    def test_english_answer_translated(self, pipeline, monkeypatch):  # 2
        monkeypatch.setattr(tr, "_chat", good_translation)
        body = post({"question": "삼성전자 어때?", "locale": "en-US"})
        assert body["locale"] == "en-US" and body["translation_status"] == "translated"
        assert "삼성전자" in body["answer"] and "is trending upward" in body["answer"]  # 종목명은 원문 그대로
        assert i18n.text("translation_note", "en-US") in body["answer"]
        assert body["disclaimer"] == i18n.disclaimer("en-US")

    def test_llm_failure_gives_fallback_summary_without_korean(self, pipeline, monkeypatch):  # 3
        pipeline["records"] = [STOCK]
        monkeypatch.setattr(tr, "_chat", lambda m: (_ for _ in ()).throw(ConnectionError("simulated")))
        body = post({"question": "삼성전자 어때?", "locale": "en-US"})
        assert body["translation_status"] == "fallback_summary" and body["locale"] == "en-US"
        assert KOREAN not in body["answer"] and body["answer"].startswith("※")
        assert "71,500" in body["answer"] and "-1.23%" in body["answer"] and "12,345,678" in body["answer"]  # 값은 그대로

    def test_validation_failure_is_same_fallback_as_llm_failure(self, pipeline, monkeypatch):  # 4
        """검증 실패(번역문에 한글이 남음)와 연결 실패를 구분하지 않는다: 둘 다 fallback_summary(번역 확인 불가 = 실패)."""
        monkeypatch.setattr(tr, "_chat", lambda m: "한국어 그대로")
        body = post({"question": "삼성전자 어때?", "locale": "ja-JP"})
        assert body["translation_status"] == "fallback_summary"
        assert body["answer"].startswith(i18n.text("translation_fallback_empty", "ja-JP"))  # 도구 결과가 없으면 빈 요약 안내

    def test_partial_chunk_failure_never_mixes_languages(self, pipeline, monkeypatch):  # 5(일부 번역 실패)
        first = "첫째 문단입니다 " * 90
        second = "둘째 실패문단입니다 " * 90
        pipeline["answer"] = first.strip() + "\n\n" + second.strip()
        assert len(tr._chunks(pipeline["answer"])) == 2
        monkeypatch.setattr(tr, "_chat", lambda m: "아직 한국어" if "실패문단" in m[1]["content"] else good_translation(m))
        body = post({"question": "삼성전자 어때?", "locale": "en-US"})
        assert body["translation_status"] == "fallback_summary"
        assert "is trending upward" not in body["answer"] and "첫째" not in body["answer"]

    def test_status_is_the_same_in_response_meta_and_saved_record(self, pipeline, monkeypatch):  # 5·6
        monkeypatch.setattr(tr, "_chat", good_translation)
        body = post({"question": "삼성전자 어때?", "locale": "zh-CN"})
        saved = pipeline["saved"][-1]
        assert saved["translation_status"] == body["translation_status"] == "translated"
        assert saved["locale"] == body["locale"] == "zh-CN"
        assert saved["answer"] == body["answer"] and saved["original_content"] == KOREAN

    def test_localized_template_answer(self, pipeline, monkeypatch):
        """고정 안내(답변 실패)는 언어별 고정 문구: localized."""
        pipeline["answer"] = agent.FALLBACK_ANSWER
        monkeypatch.setattr(tr, "_chat", lambda m: pytest.fail("고정 문구는 번역하지 않는다"))
        body = post({"question": "삼성전자 어때?", "locale": "en-US"})
        assert body["translation_status"] == "localized" and body["answer"] == i18n.text("fallback_answer", "en-US")

    @pytest.mark.parametrize("requested", ["fr-FR", "zh-TW", "de", "xx-YY"])
    def test_unsupported_locale_answers_in_korean_and_says_so(self, pipeline, monkeypatch, requested):  # 11·12
        monkeypatch.setattr(tr, "_chat", lambda m: pytest.fail("지원하지 않는 언어는 번역하지 않는다"))
        body = post({"question": "삼성전자 어때?", "locale": requested})
        assert body["locale"] == "ko-KR" and body["translation_status"] is None and body["answer"] == KOREAN
        assert body["disclaimer"] == DISCLAIMER

    @pytest.mark.parametrize("requested, actual", [("ja", "ja-JP"), ("EN_us", "en-US"), ("zh-Hans", "zh-CN"), ("  ", "ko-KR")])
    def test_locale_aliases_resolve_to_supported_code(self, pipeline, monkeypatch, requested, actual):  # 12
        monkeypatch.setattr(tr, "_chat", good_translation)
        assert post({"question": "삼성전자 어때?", "locale": requested})["locale"] == actual

    def test_fallback_summary_values_and_dates(self):  # 13
        text = quote_i18n.fallback_summary([STOCK, NEWS, DISCLOSURE], "en-US", now=datetime(2026, 11, 9, 10, tzinfo=KST))
        assert "71,500" in text and "2026-11-06" in text  # 주가 기준일·값은 그대로
        # 38차 수정: 뉴스·공시 날짜는 한국 날짜(예전에는 UTC 앞 10자 2026-10-27)
        assert "삼성전자 신제품 발표 (2026-10-28)" in text and "삼성전자 분기보고서 (2026-10-28)" in text
        assert "2026-10-27" not in text


class TestTranslationStatusContract:
    def test_post_literal_matches_values_answer_i18n_can_return(self):  # 5
        assert set(typing.get_args(research_api.TranslationStatus)) == {
            tr.STATUS_TRANSLATED, tr.STATUS_LOCALIZED, answer_i18n.STATUS_FALLBACK_SUMMARY}
        assert tr.STATUS_FALLBACK not in typing.get_args(research_api.TranslationStatus)  # 옛 기록에만 있는 값

    def test_post_and_record_detail_share_field_names(self):  # 6
        post_fields = research_api.ResearchResponse.model_fields
        record_fields = research_api.ReportRecordDetail.model_fields
        for name in ("locale", "translation_status", "answer", "used_tools", "sources", "previous_report_id", "disclaimer"):
            assert name in post_fields and name in record_fields
        assert post_fields["report_id"].annotation == int | None and record_fields["report_id"].annotation is int


# ---------- 7~10: 시간대 ----------


class TestTimestamps:
    def test_every_model_timestamp_is_timezone_aware(self):  # 9: DB에서 naive 시각이 나올 수 없다
        from app.db.base import Base
        import app.models  # noqa: F401  (모든 테이블 등록)

        cols = [(t.name, c.name) for t in Base.metadata.tables.values() for c in t.columns if isinstance(c.type, TIMESTAMP)]
        assert len(cols) >= 30
        assert [tc for t in Base.metadata.tables.values() for c in t.columns if isinstance(c.type, TIMESTAMP)
                for tc in [(t.name, c.name)] if not c.type.timezone] == []

    def test_stored_timestamp_stays_utc(self):  # 7: 저장 시각(created_at·added_at·executed_at)은 DB 값 그대로 UTC
        from types import SimpleNamespace

        from app.watchlist import item_dict

        created = datetime(2026, 11, 8, 16, 30, 5, tzinfo=timezone.utc)
        out = item_dict(SimpleNamespace(created_at=created), SimpleNamespace(id=1, ticker="005930", name="삼성전자",
                                                                            asset_type="stock"), None)
        assert out["added_at"] == "2026-11-08T16:30:05+00:00"
        assert datetime.fromisoformat(out["added_at"]) == created

    def test_computed_timestamp_is_kst_and_same_instant(self):  # 8
        utc = datetime(2026, 11, 8, 16, 30, tzinfo=timezone.utc)
        out = _iso_kst(utc)
        assert out == "2026-11-09T01:30:00+09:00" and datetime.fromisoformat(out) == utc

    def test_naive_values_are_not_shifted(self):  # 9: 시간대를 모르는 값에 시간대를 붙여 날짜를 바꾸지 않는다
        assert kst_day("2026-10-27T23:30:00") == "2026-10-27"
        assert kst_day(datetime(2026, 10, 27, 23, 30)) == "2026-10-27"
        assert kst_day("2026-10-28") == "2026-10-28" and kst_day("20261028") == "20261028"
        assert kst_day("not a date at all") == "not a date"
        assert kst_day(None) == kst_day("") == ""
        assert kst_date("2026-10-27T23:30:00") == "2026-10-27"  # outlook의 같은 규칙

    @pytest.mark.parametrize("value, expected", [
        ("2026-10-27T15:00:00+00:00", "2026-10-28"), ("2026-10-27T14:59:59+00:00", "2026-10-27"),
        ("2026-10-28T00:00:00+09:00", "2026-10-28"), ("2026-10-27 23:30:00+00:00", "2026-10-28"),
        (datetime(2026, 10, 27, 15, tzinfo=timezone.utc), "2026-10-28"),
    ])
    def test_offset_values_use_korean_date(self, value, expected):  # 7·8
        assert kst_day(value) == expected == kst_date(value)

    def test_code_summary_titles_use_korean_date(self):
        text = code_summary([NEWS, DISCLOSURE])
        assert "(2026-10-28)" in text and "2026-10-27" not in text

    def test_local_date_differs_from_utc_created_at(self):  # 10: 생성 시각(UTC)과 표시용 날짜(사용자 시간대)
        from zoneinfo import ZoneInfo

        created = datetime(2026, 11, 8, 15, 30, tzinfo=timezone.utc)
        now = created + timedelta(hours=1)
        seoul = history_dates.date_fields(created, ZoneInfo("Asia/Seoul"), now)
        ny = history_dates.date_fields(created, ZoneInfo("America/New_York"), now)
        assert seoul == {"local_date": "2026-11-09", "local_time": "00:30", "date_group": "today"}
        assert ny == {"local_date": "2026-11-08", "local_time": "10:30", "date_group": "today"}

    def test_trading_date_is_not_generation_date(self):  # 10: 거래일 날짜는 생성 날짜를 따라가지 않는다
        from app.data_status import last_business_day

        generated = datetime(2026, 11, 8, 19, 40, tzinfo=KST)  # 일요일 저녁 생성
        assert generated.date().isoformat() == "2026-11-08"
        assert last_business_day(generated.date()) == date(2026, 11, 6)  # 그때의 최신 거래일은 금요일


# ---------- 11: locale fallback(다른 API) ----------


class TestLocaleFallbackAcrossApis:
    def test_supported_locales_are_exactly_four(self):
        assert i18n.SUPPORTED_LOCALES == ("ko-KR", "en-US", "ja-JP", "zh-CN")

    @pytest.mark.parametrize("requested", ["fr-FR", "zh-TW", "ko-KP"])
    def test_disclaimer_unsupported_is_korean(self, requested):
        assert client.get(f"/api/disclaimer?locale={requested}").json() == {"text": DISCLAIMER}

    @pytest.mark.parametrize("requested, preferred, expected", [
        ("fr-FR", "en-US", "ko-KR"),  # 요청 값이 있으면 그것만 본다(지원하지 않으면 계정 설정이 아니라 ko-KR)
        (None, "en-US", "en-US"), ("", "ja-JP", "ja-JP"), ("   ", None, "ko-KR"), (None, "fr-FR", "ko-KR"),
    ])
    def test_resolution_order(self, requested, preferred, expected):
        assert i18n.resolve_locale(requested, preferred) == expected


# ---------- 14~18: 종목 매칭(가짜 종목 목록) ----------


class TestMatchingPolicy:
    NAMES = ("LG전자", "LG화학", "삼성전자", "KODEX 200", "KODEX 200선물인버스2X")

    def test_watchlist_mode_corrects_transliteration(self, make_db):  # 14
        db = make_db(*self.NAMES)
        res = resolve_company(db, "엘지전자")
        assert res.company.name == "LG전자" and res.company.ticker == "000001" and res.corrected_from == "엘지전자"

    @pytest.mark.parametrize("raw", ["엘지전자", "LG전쟈", "삼성전쟈", "삼성전"])
    def test_order_mode_rejects_non_exact(self, make_db, raw):  # 16
        res = resolve_company(make_db(*self.NAMES), raw, allow_fuzzy=False)
        assert res.company is None and res.message

    @pytest.mark.parametrize("raw, name", [("000003", "삼성전자"), ("삼성 전자", "삼성전자"), ("lg전자", "LG전자"),
                                           ("KODEX 200", "KODEX 200"), ("코덱스 200", "KODEX 200"), ("kodex200", "KODEX 200")])
    def test_order_mode_accepts_code_exact_name_and_registered_forms(self, make_db, raw, name):  # 17·18
        res = resolve_company(make_db(*self.NAMES), raw, allow_fuzzy=False)
        assert res.company is not None and res.company.name == name

    def test_etf_prefix_is_not_matched_to_leveraged_etf(self, make_db):  # 15·18: 짧은 ETF 이름이 다른 ETF로 바뀌지 않는다
        db = make_db(*self.NAMES)
        assert resolve_company(db, "KODEX 200", allow_fuzzy=False).company.name == "KODEX 200"
        assert resolve_company(db, "KODEX 200선물", allow_fuzzy=False).company is None


# ---------- 19·20: 타입·문서 ----------


class TestTypesAndDocs:
    def test_generated_types_have_new_research_fields(self):  # 19
        ts = (ROOT / "docs" / "frontend-api-types.ts").read_text(encoding="utf-8")
        block = ts.split("export interface ResearchResponse {", 1)[1].split("}", 1)[0]
        assert "locale?: string;" in block
        assert 'translation_status?: "translated" | "localized" | "fallback_summary" | null;' in block

    def test_handoff_and_api_docs_describe_post_translation_status(self):  # 20
        handoff = (ROOT / "docs" / "FRONTEND_INTEGRATION_HANDOFF.md").read_text(encoding="utf-8")
        api = (ROOT / "backend" / "API.md").read_text(encoding="utf-8")
        assert "POST 응답에는 이 값이 없다" not in handoff  # 37차 미해결 문장이 남아 있지 않다
        for value in typing.get_args(research_api.TranslationStatus):
            assert f"'{value}'" in handoff  # 핸드오프의 TranslationStatus 타입에 모든 값
        for doc in (handoff, api):
            assert '"translation_status"' in doc and '"locale"' in doc
        # 시간 필드 표: 지시서의 대상 필드가 모두 적혀 있다
        for field in ("created_at", "generated_at", "local_date", "data_as_of", "executed_at", "added_at", "event_at",
                      "price_date", "published_at", "disclosed_at", "finished_at", "queried_at", "as_of"):
            assert f"`{field}`" in handoff.split("## 15.", 1)[1]
