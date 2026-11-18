"""evals/run_multilingual.py(48차 4개 언어 실답 검사)의 판정 함수와 문항 파일 형식. DB·Ollama 없이 돈다."""

from pathlib import Path

import pytest

from app.i18n import SUPPORTED_LOCALES
from evals import run_multilingual as M

CASES = Path(__file__).resolve().parents[1] / "evals" / "multilingual_cases.txt"
NAMES = ["삼성전자", "SK하이닉스", "SK", "KODEX 200"]


def stock(name="삼성전자", end="2026-11-12"):
    return {"tool_name": "stock_tool", "result": {"found": True, "company_name": name,
                                                  "prices": [{"price_date": end, "close_price": 1.0}]}}


def test_case_file_format():
    rows = [line.split("|") for line in CASES.read_text(encoding="utf-8").splitlines() if line.strip() and not line.startswith("#")]
    assert len(rows) >= 50
    assert all(len(r) == 5 and r[0] in ("dev", "holdout") and r[2] in SUPPORTED_LOCALES for r in rows)
    assert len({r[1] for r in rows}) == len(rows)
    assert {r[2] for r in rows} == set(SUPPORTED_LOCALES)  # 4개 언어가 모두 있다


def test_holdings_detection():
    assert M.has_holdings("상위 구성종목\n1. 삼성전자 (005930) 34.19%")
    assert M.has_holdings("KODEX 200에서 삼성전자(005930)의 비중은 34.19%입니다.")
    assert not M.has_holdings("KODEX 200의 2026-11-12 종가는 84,240원, 20거래일간 -3.06%입니다.")


def test_clean_answer_has_no_checks():
    resp = {"answer": "삼성전자의 2026-11-12 종가는 208,000원입니다.", "sources": [], "meta": {}}
    assert M.check_one("d01", "ko-KR", ["삼성전자"], "삼성전자 어때?", resp, [stock()], resp["answer"], NAMES) == {}


def test_wrong_resolution_missing_date_and_changed_number_are_flagged():
    resp = {"answer": "SK closed at 1,254,001 won.", "sources": [{"url": "https://made.up/1"}],
            "meta": {"translation_status": "translated"}}
    checks = M.check_one("n04", "en-US", ["SK하이닉스"], "SK海力士最近股价怎么样？", resp, [stock("SK")],
                         "SK의 종가는 1,254,000원입니다.", NAMES)
    assert set(checks) == {"wrong_company", "bad_source_id", "number_changed", "base_date_missing"}


@pytest.mark.parametrize("answer", ["삼성전자는 2026년 11월 12일 기준 208,000원", "삼성전자 11월 12일 종가 208,000원"])
def test_korean_date_forms_count_as_base_date(answer):
    resp = {"answer": answer, "sources": [], "meta": {}}
    assert "base_date_missing" not in M.check_one("d01", "ko-KR", ["삼성전자"], "q", resp, [stock()], answer, NAMES)


def test_holding_names_from_the_template_data_are_allowed():
    answer = "KODEX 200의 구성종목과 비중입니다.\n\n상위 구성종목\n1. 삼성전자 (005930) 34.19%\n2. SK하이닉스 (000660) 27.40%"
    resp = {"answer": answer, "sources": [], "meta": {"etf_holdings": {"kind": "constituents", "views": [
        {"name": "KODEX 200", "items": [{"name": "삼성전자"}, {"name": "SK하이닉스"}]}]}}}
    assert M.check_one("e01", "ko-KR", ["KODEX 200"], "KODEX 200 구성종목", resp, [], answer, NAMES) == {}


# ---------- 49차 판정(evals/route_checks.py) ----------
from evals import route_checks as RC  # noqa: E402

CASES49 = Path(__file__).resolve().parents[1] / "evals" / "multilingual_cases_49.txt"


def test_case_file_49_format():
    rows = [line.split("|") for line in CASES49.read_text(encoding="utf-8").splitlines() if line.strip() and not line.startswith("#")]
    assert len(rows) >= 40 and all(len(r) == 6 and r[0] == "new49" for r in rows)
    assert {r[2] for r in rows} == set(SUPPORTED_LOCALES)
    assert all(RC.parse_expect(r[4]).get("route") for r in rows)
    old = {line.split("|")[-1] for line in CASES.read_text(encoding="utf-8").splitlines() if line.strip() and not line.startswith("#")}
    assert not ({r[5] for r in rows} & old)  # 48차 문항과 겹치지 않는다


def _rec(tool="stock_tool", **res):
    return {"tool_name": tool, "arguments": {"ticker": res.get("company_name"), "period_days": res.pop("pd", None)},
            "result": {"found": True, **res}}


def test_observe_and_route():
    fc = [_rec(company_name="기아", called_via="forecast_brief", period="1개월")]
    assert RC.observe(fc, {}) == {"route": "forecast", "days": 20, "companies": ["기아"]}
    assert RC.route_ok(RC.parse_expect("route=forecast,days=20"), RC.observe(fc, {}), ["기아"])
    past = [_rec(company_name="기아", pd=5, trading_days=5)]
    o = RC.observe(past, {})
    assert o["route"] == "past" and o["days"] == 5
    assert RC.period_wrong(RC.parse_expect("route=forecast,days=5"), o)  # 방향이 다르다
    assert not RC.period_wrong(RC.parse_expect("route=past,days=5"), o)
    assert RC.observe([], {"etf_holdings": {"kind": "weight"}})["route"] == "weight"
    assert RC.route_ok({"route": "reject"}, RC.observe([], {}), [])


def test_news_price_mix_judge():
    recs = [_rec(company_name="LG전자", summary="LG전자의 2026-11-12 종가는 152,300원입니다.",
                 prices=[{"price_date": "2026-11-12", "close_price": 152300.0}]),
            {"tool_name": "news_tool", "result": {"found": True, "news": [
                {"title": "LG전자 146,900원 마감", "content": "LG전자 주가가 146,900원으로 마감했다. 3분기 매출 21조 8,751억원",
                 "published_at": "2026-10-30T16:00:00+09:00"}]}}]
    assert RC.news_price_mix("## 주가 동향\nLG전자는 146,900원으로 마감했습니다.", recs) == ["146,900원"]
    assert RC.news_price_mix("## 주가 동향\nLG전자의 2026-11-12 종가는 152,300원입니다.", recs) == []
    assert RC.news_price_mix("## 원인 분석\n2026-10-30 기사에 따르면 146,900원에 마감했습니다.", recs) == []
    assert RC.news_price_mix("## 원인 분석\n30일 146,900원에 마감했습니다.", recs) == ["146,900원"]


def test_forecast_days_are_not_overwritten_by_the_market_record():
    recs = [_rec(company_name="기아", called_via="forecast_brief", period="1주일"),
            {"tool_name": "market_tool", "result": {"found": True, "company_name": "기아", "called_via": "forecast_brief"}}]
    assert RC.observe(recs, {})["days"] == 5


def test_fixed_which_company_guidance_is_not_a_wrong_company():
    guide = "어떤 종목의 주가를 볼지 알 수 없습니다. 종목 이름이나 종목코드를 넣어 다시 물어봐 주세요. 예: \"삼성전자 현재가 알려줘\""
    resp = {"answer": guide, "sources": [], "meta": {}}
    assert "wrong_company" not in M.check_one("R01", "ko-KR", [], "How is Samsung stock doing?", resp, [], guide, NAMES)


def test_judges_treat_equal_values_and_title_citations_correctly():
    assert M.numbers("209,500.0원") == M.numbers("209,500韩元")
    recs = [{"tool_name": "news_tool", "result": {"found": True, "news": [
        {"title": "KODEX 레버리지 7만635원으로 7.88% 상승…코스피200", "content": "", "published_at": "2026-10-28T09:00:00+09:00"}]}}]
    assert RC.news_price_mix("## 관련 뉴스\n- KODEX 레버리지 7만635원으로 7.88% 상승…코스피200 일간", recs) == []
    recs2 = [{"tool_name": "news_tool", "result": {"found": True, "news": [
        {"title": "기아 10월 판매", "content": "기아 판매량이 10% 증가했다.", "published_at": "2026-11-01T09:00:00+09:00"}]}}]
    assert RC.news_price_mix("기아 판매량이 10% 증가했습니다.", recs2) == []


def test_mix_judge_reads_korean_compound_amounts():
    recs = [_rec(company_name="KT&G", summary="KT&G의 2026-11-12 종가는 192,600원입니다.",
                 prices=[{"price_date": "2026-11-12", "close_price": 192600.0}]),
            {"tool_name": "news_tool", "result": {"found": True, "news": [
                {"title": "KT&G 견조", "content": "지난 2일 KT&G 주가는 19만2,600원에 거래를 마쳤다. 과징금 6억3000만원",
                 "published_at": "2026-11-03T09:00:00+09:00"}]}}]
    assert RC.news_price_mix("지난달 2일 종가 19만2,600원에 거래 마감했습니다.", recs) == []  # DB 값과 같다
    assert RC.news_price_mix("셀트리온은 과징금 6억3000만원을 부과받았다.", recs) == []
