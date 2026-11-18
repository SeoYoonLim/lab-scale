"""72차(2026-11-18) 데이터 최신성·AI 답 신뢰성. DB·Ollama 없이 돈다.

A 환율 코드 답(app/answer_fx): 하루 변동률 ≠ 기간 변동률, 5거래일·한 달 창, 주말 날짜 행, 데이터 부족, LLM 문장 수정
B 3개월 초과 원인 질문: 등락만 계산(기간 시작 전 종가가 없으면 계산 안 함), 기사는 건수만, LLM 미호출
C 복수 종목 원인 질문: 종목별 따로 계산·근거 분리, 3종목 초과 거절, 찾지 못한 종목 표시
D 뉴스·공시 freshness 필드 의미 분리(app.stock_feed.freshness)
E sources가 빈 이유(app.sources.source_status)
F 종목명 원문 유지·섹터 null, 실시간 가격과 저장 종가 구분(주문 응답)
"""
from datetime import date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace as NS

import pytest

from app import agent, answer_basis, answer_cause, answer_fx, krx_calendar, outlook
from app.sources import build_sources, source_status
from app.stock_feed import freshness
from tests.test_answer_cause_round71 import _DB, _found, idx_ok, rows_until
from tests.test_outlook_round23 import KST

NOW = datetime(2026, 11, 18, 10, 0, tzinfo=KST)
LAST = date(2026, 11, 12)
SAMSUNG = NS(id=15, name="삼성전자", ticker="005930", market="KOSPI", asset_type="stock")
HYNIX = NS(id=16, name="SK하이닉스", ticker="000660", market="KOSPI", asset_type="stock")
ECO = NS(id=13, name="에코프로비엠", ticker="247540", market="KOSDAQ", asset_type="stock")
JYP = NS(id=17, name="JYP Ent.", ticker="035900", market="KOSDAQ GLOBAL", asset_type="stock")
KAKAO = NS(id=11, name="카카오", ticker="035720", market="KOSPI", asset_type="stock")
CELL = NS(id=12, name="셀트리온", ticker="068270", market="KOSPI", asset_type="stock")


# ---------- A. 환율 ----------

def fx_rows(start=date(2026, 9, 1), end=date(2026, 11, 2), weekend=(date(2026, 10, 31),), skip=(), price=lambda i: 1300.0 + i,
            src=None):
    """평일마다 한 행(+ weekend에 준 주말 날짜 행). [(date, close, source_change_pct)] 오름차순."""
    days, d = [], start
    while d <= end:
        if (d.weekday() < 5 or d in weekend) and d not in skip:
            days.append(d)
        d += timedelta(days=1)
    rows = [(d, float(price(i)), None) for i, d in enumerate(days)]
    if src is not None:
        rows[-1] = (rows[-1][0], rows[-1][1], src)
    return rows


def fx(q, rows=None, now=NOW):
    rows = rows or fx_rows()
    res = answer_fx.compute(rows, answer_fx.spec_of(q, now), now)
    return res, answer_fx.answer(q, res, now)


def line_with(text, key):
    return next(x for x in text.splitlines() if key in x)


def test_fx_five_trading_days_uses_five_intervals_and_day_change_is_separate():
    rows = fx_rows(price=lambda i: 1300.0 + (i % 7) * 3)
    res, text = fx("최근 5거래일 동안 환율이 얼마나 변했어?", rows)
    p, d = res["period"], res["day"]
    assert (p["from_date"], p["end_date"]) == (rows[-6][0].isoformat(), rows[-1][0].isoformat())
    assert p["pct"] == round((rows[-1][1] / rows[-6][1] - 1) * 100, 2)
    assert (d["from_date"], d["end_date"]) == (rows[-2][0].isoformat(), rows[-1][0].isoformat())
    assert d["pct"] == round((rows[-1][1] / rows[-2][1] - 1) * 100, 2)
    period_line, day_line = line_with(text, "최근 5거래일"), line_with(text, "하루 변동률")
    assert f"{p['pct']:+.2f}%" in period_line and f"{d['pct']:+.2f}%" not in period_line.replace(f"{p['pct']:+.2f}%", "")
    assert f"{d['pct']:+.2f}%" in day_line and "기간 변동률이 아닙니다" in day_line


def test_fx_day_change_is_never_labeled_as_period_change_even_if_llm_wrote_it():
    fxres = {"found": True, "latest_date": "2026-11-02", "daily_change_pct": -0.93, "period_change_pct": -0.10,
             "rates": [{"price_date": "2026-10-28", "close_rate": 1326.81}, {"price_date": "2026-10-29", "close_rate": 1340.34},
                       {"price_date": "2026-11-02", "close_rate": 1325.52}]}
    llm = "원달러 환율은 최근 5일간 -0.93% 하락했습니다. 전일 대비로는 -0.93% 내렸습니다."
    out = answer_basis.ensure_basis(llm, [{"tool_name": "fx_tool", "result": fxres}], NOW)
    assert "최근 5일간 -0.93%" not in out  # 하루 값을 기간 값처럼 쓴 문장은 뺀다
    assert "전일 대비로는 -0.93%" in out  # 하루라고 밝힌 문장은 둔다
    assert "환율 수치(코드 계산, 저장 기록 기준): 마지막 기록일 하루 변동률 2026-10-29 → 2026-11-02 -1.11%" in out


def test_fx_drop_keeps_sentence_when_values_coincide():
    fxres = {"daily_change_pct": -0.5, "period_change_pct": -0.5}
    text, dropped = answer_basis.drop_fx_mislabels("원달러 환율은 최근 5일간 -0.50% 내렸습니다.", fxres)
    assert dropped == [] and "-0.50%" in text


def test_fx_one_month_starts_from_last_record_before_range_and_marks_incomplete():
    rows = fx_rows()
    res, text = fx("한 달간 원달러 환율 흐름 알려줘.", rows)
    start = date(2026, 10, 18)
    before = [r for r in rows if r[0] < start][-1]
    assert res["req_start"] == start.isoformat() and res["period"]["from_date"] == before[0].isoformat()
    assert res["period"]["end_date"] == "2026-11-02" and res["incomplete"]
    assert "기간 전체의 변동률이 아닙니다" in text


def test_fx_weekend_dated_rows_are_disclosed_and_no_date_is_invented():
    rows = fx_rows(weekend=(date(2026, 10, 31),))
    res, text = fx("요즘 원달러 환율 어때?", rows)
    stored = {r[0].isoformat() for r in rows}
    for k in ("period", "day"):
        assert res[k]["from_date"] in stored and res[k]["end_date"] in stored
    assert res["weekend_dates"] == ["2026-10-31"] and "2026-10-31(토)는 원천이 주말 날짜로 준 값" in text


def test_fx_korean_holiday_gap_is_not_filled():
    # 10-09(한글날)에 행이 없으면 그날을 만들지 않고 앞뒤 저장 행끼리 비교한다
    rows = fx_rows(start=date(2026, 10, 1), end=date(2026, 10, 13), weekend=(), skip=(date(2026, 10, 9),))
    res, _ = fx("최근 1거래일 환율 변동", rows)
    assert res["period"]["from_date"] == "2026-10-12" and res["day"]["from_date"] == "2026-10-12"
    res, _ = fx("최근 2거래일 환율 변동", rows)
    assert res["period"]["from_date"] == "2026-10-08"


def test_fx_insufficient_records_and_range_before_data_are_not_computed():
    rows = fx_rows(start=date(2026, 10, 27), end=date(2026, 11, 2), weekend=())
    res, text = fx("최근 20거래일 환율 얼마나 변했어?", rows)
    assert res["period"] is None and res["period_status"] == "short_history"
    assert "계산할 수 없습니다" in line_with(text, "최근 20거래일")
    res, text = fx("6개월 동안 환율 흐름 알려줘", rows)
    assert res["period"] is None and res["period_status"] == "short_history" and "일부 구간 값으로 대신하지 않습니다" in text


def test_fx_range_without_records_and_today_not_stored():
    res, text = fx("최근 5일 환율 상승률과 하루 변동률을 비교해 줘.")
    assert res["period_status"] == "no_data" and res["period"] is None and "저장된 환율 기록이 없어 계산하지 않았습니다" in text
    assert "하루 변동률" in text  # 하루 변동은 마지막 기록일 값으로 따로 밝힌다
    res, text = fx("원달러 환율 오늘 얼마나 올랐어?")
    assert res["period_status"] == "not_stored" and "오늘(2026-11-18) 환율은 저장돼 있지 않아" in text
    assert "11영업일 전 값입니다" in text and "최신·실시간 환율이 아니며" in text


def test_fx_source_change_pct_mismatch_is_recomputed_from_stored_rows():
    rows = fx_rows(src=-0.93)
    res, text = fx("요즘 원달러 환율 어때?", rows)
    assert res["day"]["source_change_pct"] == -0.93 and res["day"]["pct"] != -0.93
    assert "원천이 2026-11-02 행에 함께 준 등락률 -0.93%는" in text


def test_fx_cause_and_forecast_are_not_invented():
    _, text = fx("환율 왜 올랐어?")
    assert "원인을 말씀드릴 수 없습니다" in text
    _, text = fx("환율 앞으로 오를까?")
    assert "환율 전망은 제공하지 않습니다" in text


@pytest.mark.parametrize("q, companies, ok", [
    ("요즘 원달러 환율 어때?", [], True), ("최근 5거래일 동안 환율이 얼마나 변했어?", [], True),
    ("환율이 뭐야?", [], False), ("환율이 삼성전자에 미치는 영향 알려줘", [], False), ("환율 오르면 수출주 좋아?", [], False),
    ("코스피랑 환율 어때?", [], False), ("삼성전자 환율 영향", [SAMSUNG], False), ("원달러 환율 알려줘", [], True),
])
def test_fx_code_path_scope(q, companies, ok):
    assert answer_fx.is_fx_question(q, companies) is ok


def test_fx_handler_answers_without_llm_and_keeps_fx_tool_record(monkeypatch):
    monkeypatch.setattr(agent, "SessionLocal", _DB)
    monkeypatch.setattr(answer_fx, "load_rows", lambda db: fx_rows())
    monkeypatch.setattr(agent, "datetime", type("D", (datetime,), {"now": staticmethod(lambda tz=None: NOW)}))
    resp, records = agent._answer_fx("요즘 원달러 환율 어때?", [])
    assert [r["tool_name"] for r in records] == ["fx_tool"]  # 평가 tool_09(tools_all fx_tool)와 같은 기록
    assert records[0]["result"]["called_via"] == "fx_code" and resp["sources"] == []
    assert agent._answer_fx("환율이 삼성전자에 미치는 영향", []) is None
    monkeypatch.setattr(answer_fx, "load_rows", lambda db: [])
    assert "추정하지 않습니다" in agent._answer_fx("요즘 원달러 환율 어때?", [])[0]["answer"]


# ---------- B. 3개월 초과 원인 질문 ----------

def spec(q):
    return answer_cause.period_spec(q, NOW)


def test_long_period_window_requires_a_close_before_the_period():
    rows = rows_until(start=date(2026, 3, 2))
    w = answer_cause.period_window(spec("삼성전자 1년 동안 왜 올랐어?"), rows, NOW.date())
    assert w["status"] == "short_history" and w["first_stored"] == rows[0][0].isoformat() and "pct" not in w
    w = answer_cause.period_window(spec("삼성전자 6개월 동안 왜 올랐어?"), rows, NOW.date())
    assert w["status"] == "ok" and w["from_date"] == krx_calendar.previous_trading_day(date(2026, 5, 18)).isoformat()


def test_long_period_short_history_answer_has_no_numbers_or_causes():
    rows = rows_until(start=date(2026, 3, 2))
    s = spec("삼성전자 1년 동안 왜 올랐어?")
    w = answer_cause.period_window(s, rows, NOW.date())
    body = answer_cause.period_cause_answer(SAMSUNG, s, w, None, None, None, "삼성전자 1년 동안 왜 올랐어?").split("\n\n")[1]
    assert "%" not in body and "일부 구간 수치로 대신하지 않고" in body and f"{rows[0][0].isoformat()}부터" in body


def long_found(**k):
    base = {"long": True, "news": [], "disclosures": [], "news_count": 60, "news_first": "2026-09-24", "disclosure_count": 54,
            "disclosure_collection_first": "2026-06-24", "after_count": 993, "after_first": "2026-11-16", "after_close": 0,
            "unknown_news": 0, "unknown_disclosures": 0, "cutoff": "15:30"}
    return {**base, **k}


def test_long_period_answer_counts_evidence_but_does_not_pick_causes():
    q = "삼성전자 지난 6개월 동안 왜 올랐어?"
    rows = rows_until(start=date(2025, 1, 2), price=lambda i: 3000.0 - i)
    s = spec(q)
    w = answer_cause.period_window(s, rows, NOW.date())
    text = answer_cause.period_cause_answer(SAMSUNG, s, w, idx_ok(w, -28.7), long_found(), {"freshness": "3영업일 지연(미수집)"}, q)
    assert "질문의 전제와 다릅니다: 질문은 '올랐다'라고 하셨지만" in text
    assert "뉴스 60건·공시 54건 중 몇 건을 골라 주가 움직임의 원인으로 연결할 수 없습니다" in text
    assert "  - 뉴스" not in text and "  - 공시" not in text  # 기사·공시 목록을 원인 후보로 붙이지 않는다
    assert "2026-09-24부터 저장돼 있어" in text and "2026-06-24 접수분부터 수집" in text
    assert "코스피 지수: " in text and "삼성전자는 코스피" not in text


@pytest.fixture
def route(monkeypatch):
    state = {"companies": [SAMSUNG], "rows": rows_until(start=date(2025, 1, 2), price=lambda i: 3000.0 - i), "counted": [],
             "collect": [], "index": {}, "found": {}}
    monkeypatch.setattr(agent, "_question_companies", lambda q: list(state["companies"]))
    monkeypatch.setattr(agent, "_company_by_name", lambda name: None)
    monkeypatch.setattr(agent, "SessionLocal", _DB)
    monkeypatch.setattr(outlook, "price_rows", lambda db, company, now: list(state["rows"])[-260:])
    monkeypatch.setattr(answer_cause, "long_price_rows", lambda db, company, now: list(state["rows"]))
    monkeypatch.setattr(outlook, "analyze", lambda db, company, period, now, rows=None: {
        "data": {"freshness": "3영업일 지연(미수집)"}, "indicators": {}, "signals": {}, "label": "x"})

    def index_change(db, company, win):
        pct = state["index"].get(company.market, -5.74)
        name = "코스닥" if company.market == "KOSDAQ" else "코스피"
        return idx_ok(win, pct, name=name, code="KQ11" if name == "코스닥" else "KS11")

    def collect(db, company, base, start, after=None, news_limit=3, disclosure_limit=3):
        state["collect"].append(company.name)
        return state["found"].get(company.name) or _found()

    def count(db, company, frm, end):
        state["counted"].append(company.name)
        return long_found()

    monkeypatch.setattr(answer_cause, "index_change", index_change)
    monkeypatch.setattr(answer_cause, "collect", collect)
    monkeypatch.setattr(answer_cause, "count_evidence", count)
    monkeypatch.setattr(agent, "_llm_chat", lambda **k: pytest.fail("원인 질문은 채팅 LLM을 부르지 않는다"))
    monkeypatch.setattr(agent, "datetime", type("D", (datetime,), {"now": staticmethod(lambda tz=None: NOW)}))
    agent._reset_llm_state()
    return state


def test_long_period_route_is_code_path_with_counts_only(route):
    resp, records = agent._answer_forecast("삼성전자 지난 6개월 동안 왜 올랐어?", None)
    assert route["counted"] == ["삼성전자"] and route["collect"] == []
    assert resp["sources"] == [] and "원인을 특정하지 않고" in resp["answer"]
    news = next(r for r in records if r["tool_name"] == "news_tool")["result"]
    assert news["evidence_policy"] == "long_period" and news["cause_cutoff"]["policy"] == "long_period"
    st = source_status(((r["tool_name"], r["result"]) for r in records), resp["sources"])
    assert st["status"] == "long_period" and not st["is_error"] and st["market_data_as_of"] == "2026-11-12"
    assert agent._meta()["cause_period"]["long"] is True


def test_long_trading_days_question_is_code_path(route):
    resp, _ = agent._answer_forecast("삼성전자 100거래일 동안 왜 올랐어?", None)
    assert "최근 100거래일" in resp["answer"] and "질문의 전제와 다릅니다" in resp["answer"]


def test_subjectless_cause_question_without_previous_company_asks_for_subject(route):
    route["companies"] = []
    resp, records = agent._answer_forecast("최근 6개월 동안 상승한 이유가 뭐야?", None)
    assert resp["answer"] == answer_cause.ASK_CAUSE_SUBJECT and records == []
    assert agent._forecast_subject("최근 6개월 동안 코스피가 왜 올랐어?", None) is None  # 시장 질문은 시장 원인 경로


# ---------- C. 복수 종목 ----------

def found_for(name, url):
    return {**_found(), "news": [{"company_name": name, "title": f"{name} 신규 수주", "source": "x", "url": url,
                                  "published_at": "2026-11-10T10:00:00+09:00", "date": "2026-11-10"}],
            "disclosures": [], "after_count": 10, "after_first": "2026-11-16", "after_close": 1}


def test_two_companies_are_computed_and_sourced_separately(route):
    route["companies"] = [SAMSUNG, ECO]
    route["rows"] = rows_until(start=date(2026, 6, 1), price=lambda i: 2000.0 - i)
    route["index"] = {"KOSPI": -5.74, "KOSDAQ": 3.1}
    route["found"] = {"삼성전자": found_for("삼성전자", "https://n/1"), "에코프로비엠": found_for("에코프로비엠", "https://n/2")}
    resp, records = agent._answer_forecast("삼성전자와 에코프로비엠 최근 한 달 동안 왜 올랐어?", None)
    text = resp["answer"]
    s1, s2 = text.split("### 1. 삼성전자")[1].split("### 2. 에코프로비엠")
    assert "에코프로비엠 신규 수주" not in s1 and "삼성전자 신규 수주" not in s2  # 다른 종목 기사가 붙지 않는다
    assert "코스피 지수: " in s1 and "코스닥" not in s1 and "코스닥 지수: " in s2 and "-5.74%" not in s2
    assert route["collect"] == ["삼성전자", "에코프로비엠"]
    assert {s["url"]: s["company_names"] for s in resp["sources"]} == {"https://n/1": ["삼성전자"], "https://n/2": ["에코프로비엠"]}
    assert text.count("이 서비스는") == 1  # 면책은 한 번
    multi = agent._meta()["cause_multi"]
    assert [c["company"] for c in multi["companies"]] == ["삼성전자", "에코프로비엠"] and multi["mode"] == "cause_period"
    st = source_status(((r["tool_name"], r["result"]) for r in records), resp["sources"])
    assert st["status"] == "available" and st["excluded_after_cutoff_count"] == 22  # 종목마다 (10 + 1), 뉴스·공시 기록 중복 없이


def test_same_article_linked_to_both_companies_keeps_both_names():
    a = {"found": True, "company_name": "삼성전자", "news": [{"title": "삼성전자·SK하이닉스 HBM", "company_name": "삼성전자", "url": "u"}]}
    b = {"found": True, "company_name": "SK하이닉스", "news": [{"title": "삼성전자·SK하이닉스 HBM", "company_name": "SK하이닉스", "url": "u"}]}
    out = build_sources([("news_tool", a), ("news_tool", b)])
    assert len(out) == 1 and out[0]["company_names"] == ["삼성전자", "SK하이닉스"]


def test_multi_summary_compares_only_when_dates_match():
    recs = lambda f, e, p: [{"tool_name": "stock_tool", "result": {"period_start_date": f, "period_end_date": e, "period_change_pct": p}}]
    text = answer_cause.multi_cause_answer([(SAMSUNG, "A", recs("2026-10-16", "2026-11-12", -3.48)),
                                            (HYNIX, "B", recs("2026-10-16", "2026-11-12", -1.42))], "q")
    assert "SK하이닉스가 삼성전자보다 2.06%p 높았습니다. 수익률 차이가 원인을 뜻하지는 않습니다." in text
    text = answer_cause.multi_cause_answer([(SAMSUNG, "A", recs("2026-10-16", "2026-11-12", -3.48)),
                                            (HYNIX, "B", recs("2026-10-15", "2026-11-11", -1.42))], "q")
    assert "직접 비교하지 않습니다" in text and "%p" not in text


def test_multi_cause_only_mode_runs_single_company_body_per_company(route, monkeypatch):
    route["companies"] = [KAKAO, CELL]
    calls = []

    def body(question, previous, company, marker, mode):
        calls.append((company.name, mode))
        return agent._code_answer(f"{company.name} 단락\n\n이 서비스는 면책", [
            {"tool_name": "stock_tool", "result": {"company_name": company.name, "found": True}}])

    monkeypatch.setattr(agent, "_forecast_body", body)
    resp, _ = agent._answer_forecast("카카오와 셀트리온 하락 원인을 비교해 줘.", None)
    assert calls == [("카카오", "cause"), ("셀트리온", "cause")]
    assert "### 1. 카카오\n\n카카오 단락" in resp["answer"] and "### 2. 셀트리온\n\n셀트리온 단락" in resp["answer"]


def test_more_than_three_companies_is_refused(route):
    route["companies"] = [SAMSUNG, HYNIX, KAKAO, CELL]
    resp, records = agent._answer_forecast("삼성전자 SK하이닉스 카카오 셀트리온 왜 떨어졌어?", None)
    assert "3종목까지만" in resp["answer"] and records == [] and route["collect"] == []


@pytest.mark.parametrize("q, found, missing", [
    ("카카오랑 삼송전자 왜 떨어졌어?", [KAKAO], ["삼송전자"]), ("삼송전자와 카카오 왜 떨어졌어?", [KAKAO], ["삼송전자"]),
    ("카카오와 관련된 뉴스 때문에 왜 떨어졌어?", [KAKAO], []), ("삼성전자와 코스피 왜 떨어졌어?", [SAMSUNG], []),
    ("삼성전자와 SK하이닉스 최근 한 달 동안 왜 올랐어?", [SAMSUNG, HYNIX], []), ("카카오 지난주 왜 올랐어?", [KAKAO], []),
])
def test_unmatched_second_company_is_named(q, found, missing):
    assert answer_cause.unmatched_names(q, found) == missing


def test_unmatched_note_is_prepended_on_single_company_cause_answer(route):
    route["companies"] = [SAMSUNG]
    route["rows"] = rows_until(start=date(2026, 6, 1))
    resp, _ = agent._answer_forecast("삼성전자랑 삼송전기 지난주 왜 떨어졌어?", None)
    assert resp["answer"].startswith("- 질문의 '삼송전기'는 등록된 종목에서 찾지 못해 이 답에 넣지 않았습니다")


# ---------- 시장 원인 ----------

def test_market_cause_answer_states_index_moves_and_no_cause():
    s = answer_cause.market_spec("코스피 한 달 동안 왜 올랐어?", NOW)
    rows = rows_until(start=date(2026, 6, 1), price=lambda i: 5000.0 - i)
    w = answer_cause.period_window(s, rows, NOW.date())
    text = answer_cause.market_cause_answer(s, [("코스피", w)], "코스피 한 달 동안 왜 올랐어?")
    assert "코스피 지수('한 달'): " in text and "원인을 특정할 수 없습니다" in text
    assert "질문의 전제와 다릅니다" in text and "지수 주가" not in text


def test_market_cause_today_not_stored_is_not_called_today():
    s = answer_cause.market_spec("오늘 증시 왜 빠졌어?", NOW)
    rows = rows_until(start=date(2026, 6, 1))
    w = answer_cause.period_window({**s, "too_long": False}, rows, NOW.date())
    text = answer_cause.market_cause_answer(s, [("코스피", w)], "오늘 증시 왜 빠졌어?")
    assert "'오늘'(2026-11-18) 지수 종가는 아직 저장되지 않아" in text and "코스피 지수(오늘)" not in text
    assert "코스피 지수(마지막 저장 거래일 하루): 2026-11-11" in text
    assert answer_cause.market_codes("오늘 증시 왜 빠졌어?") == ["KS11", "KQ11"] and answer_cause.market_codes("코스닥 왜") == ["KQ11"]


# ---------- D. freshness ----------

def ts(s):
    return datetime.fromisoformat(s)


def test_freshness_separates_data_time_success_and_attempt():
    summary = {"last_successful_collection_at": "2026-11-17T09:34:14+09:00", "last_attempt_finished_at": "2026-11-17T09:34:14+09:00",
               "last_collection_status": "success", "last_collection_step": "news_recovery"}
    legacy = {"step": "news", "status": "success", "ok": True, "finished_at": "2026-11-03T19:54:42+09:00", "error_code": None}
    f = freshness(ts("2026-11-17T09:34:14+09:00"), ts("2026-11-17T09:16:00+09:00"), date(2026, 11, 18), legacy,
                  ts("2026-11-17T09:32:00+09:00"), summary)
    assert f["last_collection"] == legacy  # 기존 키 뜻은 그대로(news 단계)
    assert f["last_successful_collection_at"].startswith("2026-11-17") and f["last_collection_step"] == "news_recovery"
    assert f["latest_record_at"].startswith("2026-11-17T09:32") and f["freshness_state"] == "fresh" and f["stale"] is False


def test_freshness_state_is_about_data_not_collection_status():
    failed = {"last_successful_collection_at": "2026-11-10T20:00:00+09:00", "last_attempt_finished_at": "2026-11-17T20:00:00+09:00",
              "last_collection_status": "failed", "last_collection_step": "news"}
    f = freshness(ts("2026-11-17T09:00:00+09:00"), None, date(2026, 11, 18), None, ts("2026-11-17T08:00:00+09:00"), failed)
    assert f["freshness_state"] == "fresh" and f["last_collection_status"] == "failed"  # 최신성과 수집 성공은 따로
    old = freshness(ts("2026-11-03T19:54:00+09:00"), None, date(2026, 11, 18), None, ts("2026-11-03T19:00:00+09:00"), {})
    assert old["freshness_state"] == "stale" and old["record_lag_weekdays"] == 11 and old["last_successful_collection_at"] is None
    none = freshness(None, None, date(2026, 11, 18))
    assert none["freshness_state"] == "no_data" and none["stale"] is True


# ---------- E. sources 빈 이유 ----------

def rec(tool, **result):
    return (tool, {"found": False, "company_name": "삼성전자", **result})


def test_source_status_states():
    assert source_status([("stock_tool", {"found": True})], [])["status"] == "not_applicable"
    assert source_status([rec("news_tool", news=[])], [])["status"] == "none_found"
    cut = {"base_date": "2026-11-12", "cutoff": "15:30", "evidence_cutoff": "2026-11-12T15:30:00+09:00", "policy": "cause_window",
           "excluded_after": 993, "excluded_unknown_time": 0}
    st = source_status([rec("news_tool", cause_cutoff=cut), rec("disclosure_tool", cause_cutoff=cut)], [])
    assert st["status"] == "excluded_after_cutoff" and st["excluded_after_cutoff_count"] == 993  # 같은 종목 두 기록을 두 번 세지 않는다
    assert "993건" in st["message"] and st["evidence_cutoff"] == "2026-11-12T15:30:00+09:00" and not st["is_error"]
    err = source_status([rec("news_tool", error="news_tool 실행 중 오류: X")], [])
    assert err["status"] == "error" and err["is_error"]
    ok = source_status([("news_tool", {"found": True})], [{"type": "news"}])
    assert ok["status"] == "available" and ok["empty_reason"] is None and ok["message"] is None


def test_source_status_llm_path_counts_each_filtered_record():
    a = {"base_date": "2026-11-12", "cutoff": "15:30", "excluded_after": 3, "excluded_unknown_time": 1}
    st = source_status([rec("news_tool", cause_cutoff=a), rec("rag_search_tool", cause_cutoff=dict(a))], [])
    assert st["excluded_after_cutoff_count"] == 6 and st["excluded_unknown_time_count"] == 2
    assert st["evidence_cutoff"] == "2026-11-12T15:30:00+09:00"


def test_cause_only_records_carry_cutoff_for_source_status():
    info = answer_cause.cause_cutoff_info("2026-11-12", {"after_count": 993, "after_close": 2, "unknown_news": 1, "cutoff": "15:30"})
    assert info["excluded_after"] == 995 and info["excluded_unknown_time"] == 1 and info["evidence_cutoff"].startswith("2026-11-12T15:30")


def test_research_response_accepts_source_status():
    from app.api.research import ResearchResponse

    st = source_status([rec("news_tool")], [])
    r = ResearchResponse(answer="x", used_tools=["news_tool"], sources=[], source_status=st)
    assert r.source_status.status == "none_found" and ResearchResponse(answer="x", used_tools=[]).source_status is None


# ---------- F. 종목명·섹터·가격 기준 ----------

def test_official_english_name_is_used_as_is_in_answers(route):
    route["companies"] = [JYP]
    route["rows"] = rows_until(start=date(2026, 6, 1))
    text = agent._answer_forecast("JYP Ent. 지난주 왜 떨어졌어?", None)[0]["answer"]
    assert text.startswith("JYP Ent.(035900) · '지난주'") and "제이와이피" not in text


def test_company_api_keeps_null_sector():
    from app.api.companies import CompanyItem

    assert CompanyItem(ticker="005930", name="삼성전자", market="KOSPI", sector=None, asset_type="stock").sector is None


def test_order_response_carries_price_source_and_as_of(monkeypatch):
    import app.portfolio as portfolio
    from tests.test_portfolio_order_response import FakeSession

    account = NS(cash_balance=Decimal("10000000"))
    monkeypatch.setattr(portfolio, "_lock_account", lambda db, key: account)
    monkeypatch.setattr(portfolio, "get_realtime_price_data", lambda db, c: {
        "current_price": 189800.0, "is_realtime": True, "source": "naver", "as_of": "2026-11-18T10:01:02+09:00"})
    out = portfolio.place_order(FakeSession(None), "owner", SAMSUNG, "buy", 1)
    assert (out["price"], out["price_source"], out["price_is_realtime"], out["price_as_of"]) == (
        189800.0, "naver", True, "2026-11-18T10:01:02+09:00")
    monkeypatch.setattr(portfolio, "get_realtime_price_data", lambda db, c: {
        "current_price": 208000.0, "is_realtime": False, "source": "fallback", "as_of": "2026-11-12"})
    out = portfolio.place_order(FakeSession(None), "owner", SAMSUNG, "buy", 1)
    assert out["price_source"] == "fallback" and out["price_as_of"] == "2026-11-12" and out["price_is_realtime"] is False
