"""71차(2026-11-18) 기간을 붙인 원인 질문. DB·Ollama 없이 돈다(시세는 실제 KRX 달력의 거래일로 만든 가짜 값).

70차 실검증 문제(docs/FINAL_INTEGRATION_AUDIT_70.md 5.2): (10) 셀트리온 '지난주 급등'인데 실제 -0.71%를 바로잡지 않음, (2b) 코스피 -16.31%를
카카오 주어에 붙임, 근거 없이 "시장 흐름과 관련". 이 파일은 기간 해석, 종목·지수 분리 계산, 전제 확인, 근거 창, 데이터 부족 안내,
경로 분기(코드 답 / 기존 경로)를 본다.
"""
from datetime import date, datetime, timedelta
from types import SimpleNamespace as NS

import pytest

from app import agent, answer_cause, krx_calendar, outlook
from tests.test_outlook_round23 import KST

NOW = datetime(2026, 11, 18, 10, 0, tzinfo=KST)  # 화요일 장중. 저장 시세는 11-12(목)까지(70차 실데이터와 같은 모양)
LAST = date(2026, 11, 12)
KAKAO = NS(id=11, name="카카오", ticker="035720", market="KOSPI", asset_type="stock")
CELL = NS(id=12, name="셀트리온", ticker="068270", market="KOSPI", asset_type="stock")
ECO = NS(id=13, name="에코프로비엠", ticker="247540", market="KOSDAQ", asset_type="stock")
KODEX = NS(id=14, name="KODEX 200", ticker="069500", market=None, asset_type="etf")
SAMSUNG = NS(id=15, name="삼성전자", ticker="005930", market="KOSPI", asset_type="stock")


def rows_until(last=LAST, start=date(2026, 6, 1), skip=(), price=lambda i: 1000.0 + i):
    """KRX 거래일마다 종가 price(i)인 시세 [(date, close, volume)]. skip의 날짜는 빼서 '저장 안 된 거래일'을 만든다."""
    days = [d for d in krx_calendar.trading_days(start, last) if d not in set(skip)]
    return [(d, price(i), 1000) for i, d in enumerate(days)]


def spec(q, now=NOW):
    return answer_cause.period_spec(q, now)


def window(q, rows=None, now=NOW):
    return answer_cause.period_window(spec(q, now), rows or rows_until(), now.date())


def close_on(rows, d):
    return next(c for x, c, _ in rows if x == d)


def idx_ok(win, pct, name="코스피", code="KS11"):
    return {"status": "ok", "code": code, "name": name, "from_date": win["from_date"], "end_date": win["end_date"],
            "from_close": 5000.0, "end_close": round(5000.0 * (1 + pct / 100), 2), "pct": pct}


def answer(company, q, win, idx=None, found=None, data=None):
    return answer_cause.period_cause_answer(company, spec(q), win, idx, found, data, q)


# ---------- 1. 기간 해석(KRX 거래일) ----------

def test_last_week_is_mon_to_fri_and_starts_from_previous_friday_close():
    rows = rows_until()
    w = window("셀트리온 지난주 급등 이유", rows)
    assert (w["req_start"], w["req_end"]) == ("2026-11-09", "2026-11-15")
    assert w["window_days"] == ["2026-11-09", "2026-11-10", "2026-11-11", "2026-11-12", "2026-11-13"]
    assert w["from_date"] == "2026-11-06" and w["end_date"] == "2026-11-12" and w["trading_days"] == 4
    assert w["missing_tail"] == ["2026-11-13"]  # 지난주 금요일 시세가 아직 없다 -> 끝나지 않은 기간
    f, e = close_on(rows, date(2026, 11, 6)), close_on(rows, date(2026, 11, 12))
    assert w["pct"] == round((e / f - 1) * 100, 2)


def test_last_month_uses_calendar_range_and_krx_holidays():
    w = window("현대차 지난달 왜 떨어졌어?")
    assert (w["req_start"], w["req_end"]) == ("2026-10-01", "2026-10-31")
    october = krx_calendar.trading_days(date(2026, 10, 1), date(2026, 10, 31))
    assert w["window_days"] == [d.isoformat() for d in october]
    assert all(krx_calendar.is_trading_day(date.fromisoformat(d)) is not False for d in w["window_days"])  # 휴장일을 넣지 않는다
    assert w["from_date"] == krx_calendar.previous_trading_day(october[0]).isoformat() and w["end_date"] == october[-1].isoformat()
    assert w["missing_tail"] == [] and w["missing_mid"] == []


def test_n_trading_days_count_back_from_the_price_base_date():
    w = window("삼성전자 최근 5거래일 동안 왜 올랐어?")
    assert w["window_days"] == ["2026-11-06", "2026-11-09", "2026-11-10", "2026-11-11", "2026-11-12"]
    assert w["from_date"] == "2026-11-05" and w["end_date"] == "2026-11-12" and w["trading_days"] == 5
    w20 = window("SK하이닉스 지난 20거래일 하락 이유")
    assert len(w20["window_days"]) == 20 and w20["end_date"] == "2026-11-12"


def test_one_month_up_to_today_marks_unstored_days_as_incomplete():
    w = window("카카오 한 달 동안 왜 떨어졌어?")
    assert (w["req_start"], w["req_end"]) == ("2026-10-18", "2026-11-18")
    assert w["end_date"] == "2026-11-12" and w["missing_tail"] == ["2026-11-13", "2026-11-16", "2026-11-17", "2026-11-18"]


def test_this_week_with_no_stored_price_is_no_data_not_estimated():
    w = window("KODEX 200 이번 주 왜 빠졌어?")
    assert w["status"] == "no_data" and "from_date" not in w and "pct" not in w
    text = answer(KODEX, "KODEX 200 이번 주 왜 빠졌어?", w)
    assert "시세가 아직 저장되지 않았습니다" in text and "추정해 채우지 않습니다" in text and "%" not in text.split("\n\n")[1]


def test_missing_trading_day_in_the_middle_is_reported_and_not_filled():
    rows = rows_until(skip=(date(2026, 11, 10),))
    w = window("셀트리온 지난주 왜 떨어졌어?", rows)
    assert w["missing_mid"] == ["2026-11-10"] and w["status"] == "ok"
    text = answer(CELL, "셀트리온 지난주 왜 떨어졌어?", w)
    assert "기간 중간에 시세가 저장되지 않은 거래일 1일(2026-11-10)" in text and "빠진 날의 값은 만들지 않았습니다" in text


def test_missing_previous_close_uses_the_last_stored_day_before_and_says_so():
    rows = rows_until(skip=(date(2026, 11, 6),))
    w = window("셀트리온 지난주 왜 떨어졌어?", rows)
    assert w["from_date"] == "2026-11-05" and w["expected_from"] == "2026-11-06" and not w["from_is_prev_trading_day"]
    assert "기간 직전 거래일 2026-11-06의 시세가 저장돼 있지 않아 그 전 저장 거래일 2026-11-05 종가를" in answer(
        CELL, "셀트리온 지난주 왜 떨어졌어?", w)


def test_short_history_starts_from_first_stored_day_and_is_not_called_full_period():
    rows = rows_until(start=date(2026, 10, 20))
    w = window("카카오 지난달 왜 떨어졌어?", rows)
    assert w["start_partial"] and w["from_date"] == "2026-10-20"
    text = answer(KAKAO, "카카오 지난달 왜 떨어졌어?", w)
    assert "저장 시세가 2026-10-20부터라" in text and "요청 기간 전체의 등락이 아닙니다" in text


def test_price_stops_mid_period_and_tail_is_disclosed():
    rows = rows_until(last=date(2026, 10, 15))
    w = answer_cause.period_window(spec("현대차 지난달 왜 떨어졌어?"), rows, NOW.date())
    assert w["end_date"] == "2026-10-15" and w["missing_tail"][0] == "2026-10-16"
    text = answer(NS(id=1, name="현대차", ticker="005380", market="KOSPI", asset_type="stock"), "현대차 지난달 왜 떨어졌어?", w)
    assert "끝나지 않은 기간이라 이 수치는 기간 전체 등락이 아닙니다" in text


# ---------- 2. 종목·지수 분리, 전제 확인 ----------

def _down_rows():
    return rows_until(price=lambda i: 2000.0 - i)  # 매일 내린다


def test_premise_up_but_actually_down_is_corrected_first():
    q = "셀트리온이 지난주 급등한 이유가 뭐야?"
    w = window(q, _down_rows())
    assert w["pct"] < 0
    text = answer(CELL, q, w, idx_ok(w, 5.59))
    assert f"질문의 전제와 다릅니다: 질문은 '급등'이라고 하셨지만, 셀트리온 주가는 이 기간 {w['pct']:+.2f}%로 내렸습니다" in text


def test_premise_down_but_actually_up_is_corrected():
    q = "삼성전자 최근 5거래일 동안 왜 떨어졌어?"
    w = window(q)
    assert w["pct"] > 0
    assert "질문은 '내렸다'라고 하셨지만, 삼성전자 주가는 이 기간" in answer(SAMSUNG, q, w, idx_ok(w, -0.76))


def test_same_direction_premise_is_confirmed_not_amplified():
    q = "카카오 한 달 동안 왜 떨어졌어?"
    w = window(q, _down_rows())
    text = answer(KAKAO, q, w, idx_ok(w, -5.74))
    assert "질문의 전제('내렸다')와 같은 방향입니다" in text


def test_stock_and_index_returns_are_on_separate_subject_lines():
    q = "카카오 한 달 동안 왜 떨어졌어?"
    w = window(q, _down_rows())
    text = answer(KAKAO, q, w, idx_ok(w, -16.31))
    stock_line = next(ln for ln in text.splitlines() if ln.startswith("- 카카오(종목) 주가:"))
    index_line = next(ln for ln in text.splitlines() if " 지수: " in ln)
    assert f"{w['pct']:+.2f}%" in stock_line and "-16.31%" not in stock_line
    assert "코스피 지수" in index_line and "-16.31%" in index_line
    assert "카카오는 코스피" not in text  # 70차 2b의 주어 혼동 문장


def test_market_down_stock_up_and_market_up_stock_down():
    q = "카카오 지난주 왜 올랐어?"
    w = window(q)
    up = answer(KAKAO, q, w, idx_ok(w, -3.0))
    assert "코스피 지수보다" in up and "높았습니다" in up
    w2 = window(q, _down_rows())
    down = answer(KAKAO, q, w2, idx_ok(w2, 5.0))
    assert "낮았습니다" in down


@pytest.mark.parametrize("pct", [-5.74, 5.59, 0.0])
def test_index_move_is_never_stated_as_the_cause(pct):
    q = "카카오 한 달 동안 왜 떨어졌어?"
    w = window(q, _down_rows())
    text = answer(KAKAO, q, w, idx_ok(w, pct))
    assert "같은 방향(또는 반대 방향)으로 움직였다는 사실만으로 원인을 판단하지 않습니다" in text
    for bad in ("시장 흐름과 관련", "시장 영향으로", "지수 하락 때문", "지수 상승 때문", "시장 탓"):
        assert bad not in text


def test_kosdaq_stock_uses_kosdaq_name_and_etf_says_no_index():
    q = "에코프로비엠 2주 동안 왜 빠졌어?"
    w = window(q)
    assert "코스닥 지수" in answer(ECO, q, w, idx_ok(w, -7.53, "코스닥", "KQ11"))
    qe = "KODEX 200 지난주 왜 빠졌어?"
    we = window(qe)
    text = answer(KODEX, qe, we, {"status": "no_index"})
    assert "ETF라 상장 시장 지수 정보가 없어 시장 지수와 비교하지 않았습니다" in text
    assert "구성 종목이나 지수의 등락이 아닙니다" in text and " 지수: " not in text


def test_missing_index_close_on_same_dates_skips_comparison():
    q = "카카오 지난주 왜 떨어졌어?"
    w = window(q)
    text = answer(KAKAO, q, w, {"status": "missing", "code": "KS11", "name": "코스피", "missing": [w["end_date"]]})
    assert f"같은 날짜({w['end_date']})의 코스피 지수 종가가 없어 지수와 비교하지 않았습니다" in text and " 지수: " not in text


# ---------- 3. 근거(기간 창·기준 시점) ----------

def _found(news=(), discs=(), **kw):
    base = {"news": list(news), "disclosures": list(discs), "after_count": 0, "after_first": None, "after_close": 0,
            "unknown_news": 0, "unknown_disclosures": 0, "cutoff": "15:30", "news_candidates": len(news), "price_reports": 0}
    base.update(kw)
    return base


def test_no_news_no_disclosure_says_cause_cannot_be_identified():
    q = "카카오 한 달 동안 왜 떨어졌어?"
    w = window(q)
    text = answer(KAKAO, q, w, idx_ok(w, -5.0), _found())
    assert "직접 다룬 기사·공시를 찾지 못해 움직임의 원인을 특정할 수 없습니다" in text
    assert "카카오를 직접 다룬" in text  # 조사(을/를)


def test_news_only_or_disclosure_only_are_listed_with_dates_and_sources():
    q = "셀트리온 지난주 왜 떨어졌어?"
    w = window(q)
    n = {"company_name": "셀트리온", "title": "셀트리온, 비세큐마 호주 허가 신청", "source": "가신문", "date": "2026-11-12",
         "url": "https://n/1", "published_at": "2026-11-12T10:00:00+09:00"}
    d = {"company_name": "셀트리온", "title": "주요사항보고서(자기주식취득결정)", "date": "2026-11-10", "source_url": "https://d/1"}
    only_news = answer(CELL, q, w, None, _found([n]))
    assert "뉴스 2026-11-12 (가신문) \"셀트리온, 비세큐마 호주 허가 신청\"" in only_news and "공시 " not in only_news
    only_disc = answer(CELL, q, w, None, _found(discs=[d]))
    assert "공시 2026-11-10 접수 \"주요사항보고서(자기주식취득결정)\"" in only_disc
    assert "직접 원인이라고 단정할 수는 없습니다" in only_news and "직접 원인이라고 단정할 수는 없습니다" in only_disc


def test_after_base_and_after_close_and_unknown_time_items_are_counted_not_used():
    q = "카카오 한 달 동안 왜 떨어졌어?"
    w = window(q)
    text = answer(KAKAO, q, w, None, _found(after_count=674, after_first="2026-11-16", after_close=2, unknown_disclosures=1))
    assert "시세 기준일 뒤(2026-11-16~)에 수집된 카카오 기사 674건은 2026-11-12 시세에 반영되지 않아" in text
    assert "2026-11-12 장 마감(15:30) 뒤에 나온 기사 2건" in text and "접수 시각이 없는 공시 1건" in text
    assert "원인을 특정할 수 없습니다" in text  # 기준일 뒤 기사만 있으면 원인 후보가 없다


def test_price_report_articles_are_counted_not_used_as_causes():
    assert answer_cause.is_price_report("거래대금 73,724백만원 셀트리온, 상승 마감 전 흐름")
    assert answer_cause.is_price_report("최근 5거래일 카카오 주가 3만원대 이탈")
    assert answer_cause.is_price_report("코스피, 3.59% 하락…삼전 4.2%·하이닉스 5.4%↓")
    assert answer_cause.is_price_report("KODEX 200 8만4305원…거래대금 1조7379억원으로 2.81%↑")
    assert not answer_cause.is_price_report("[컨콜] SK하이닉스 \"현 주가 저평가…자사주 매입·소각 지속\"")
    assert not answer_cause.is_price_report("셀트리온, 비세큐마 호주 품목허가 신청")
    q = "셀트리온 지난주 왜 떨어졌어?"
    w = window(q)
    text = answer(CELL, q, w, None, _found(price_reports=3))
    assert "주가·거래 동향을 전한 기사 3건은 움직임을 묘사한 기사라 원인 후보로 쓰지 않았습니다" in text


def test_evidence_window_starts_after_the_previous_close():
    # 기간 직전 거래일(출발 종가) 장 마감 전 기사는 그 종가에 이미 반영됐다 -> 기간 움직임의 후보가 아니다
    frm = date(2026, 11, 6)
    cut = answer_cause.cutoff_at(frm)
    assert answer_cause.time_state(datetime(2026, 11, 6, 15, 0, tzinfo=KST).isoformat(), frm) == "before"
    assert cut == datetime(2026, 11, 6, 15, 30, tzinfo=KST)


def test_etf_answer_says_articles_only():
    q = "KODEX 200 지난주 왜 빠졌어?"
    w = window(q)
    assert "직접 다룬 기사를 찾지 못해" in answer(KODEX, q, w, {"status": "no_index"}, _found())


def test_stale_data_is_disclosed():
    q = "카카오 한 달 동안 왜 떨어졌어?"
    w = window(q)
    text = answer(KAKAO, q, w, None, _found(), {"freshness": "3영업일 지연(미수집)"})
    assert "시세가 3영업일 지연(미수집) 상태입니다(마지막 저장 거래일 2026-11-12)" in text


# ---------- 4. 잘못된·미래 기간 ----------

@pytest.mark.parametrize("q, kind", [
    ("삼성전자 다음 주 왜 올랐어?", "future"),
    ("삼성전자 내일 왜 떨어졌어?", "future"),
    ("삼성전자 0거래일 동안 왜 떨어졌어?", "invalid"),
    ("삼성전자 지난 0일 동안 왜 올랐어?", "invalid"),
    ("삼성전자 13월에 왜 올랐어?", "unsupported"),
    ("삼성전자 2025년에 왜 올랐어?", "unsupported"),
])
def test_future_invalid_unparsed_periods_are_not_replaced_by_another_window(q, kind):
    s = spec(q)
    assert s and s["kind"] == kind and answer_cause.is_period_cause_question(q, NOW)
    assert not answer_cause.is_cause_only_question(q)  # 69차 원인 단독(최근 5거래일)으로 바꿔 답하지 않는다
    text = answer_cause.period_cause_answer(SAMSUNG, s, {"status": kind, "last_stored": "2026-11-12"}, None, None, None, q)
    body = text.split("\n\n")[1]
    assert "%" not in body and "종가" not in body and "원인 후보" not in body  # 다른 기간으로 계산한 수치·근거가 없다


@pytest.mark.parametrize("q", ["삼성전자 4개월 동안 왜 떨어졌어?", "삼성전자 100거래일 동안 왜 올랐어?", "삼성전자 지난 1년 동안 왜 올랐어?",
                               "삼성전자 올해 왜 올랐어?"])
def test_longer_than_three_months_stays_on_existing_path(q):
    assert spec(q)["too_long"] and not answer_cause.is_period_cause_question(q, NOW)


@pytest.mark.parametrize("q", ["카카오 3개월 동안 왜 떨어졌어?", "카카오 최근 60거래일 왜 올랐어?", "카카오 2주 동안 왜 빠졌어?",
                               "카카오 10일 동안 왜 빠졌어?", "카카오 이번 달 왜 올랐어?", "카카오 최근 한 달 동안 왜 상승했어?",
                               "SK하이닉스 지난 20거래일 하락 이유", "셀트리온 지난주 급등 이유"])
def test_period_cause_questions_within_three_months(q):
    assert answer_cause.is_period_cause_question(q, NOW)


@pytest.mark.parametrize("q", ["셀트리온 지난주 실적 발표 후 왜 떨어졌어?", "카카오 한 달 동안 왜 떨어졌어? 앞으로 오를까?",
                               "카카오 지난주 왜 올랐는지 보고 매수해줘", "카카오 지난주 뉴스 알려줘", "카카오 지난주 주가 알려줘"])
def test_not_period_cause_questions(q):
    assert not answer_cause.is_period_cause_question(q, NOW)


# ---------- 5. 경로 분기(agent) ----------

class _DB:
    def close(self):
        pass


@pytest.fixture
def route(monkeypatch):
    state = {"companies": [KAKAO], "rows": rows_until(price=lambda i: 2000.0 - i), "collect": [], "index": None,
             "found": _found(), "by_name": {"카카오": KAKAO, "셀트리온": CELL, "KODEX 200": KODEX}}

    monkeypatch.setattr(agent, "_question_companies", lambda q: list(state["companies"]))
    monkeypatch.setattr(agent, "_company_by_name", lambda name: state["by_name"].get(name))
    monkeypatch.setattr(agent, "SessionLocal", _DB)
    monkeypatch.setattr(outlook, "price_rows", lambda db, company, now: list(state["rows"]))
    monkeypatch.setattr(outlook, "analyze", lambda db, company, period, now, rows=None: {
        "data": {"freshness": "3영업일 지연(미수집)"}, "indicators": {}, "signals": {}, "label": "x"})

    def index_change(db, company, win):
        return state["index"] or idx_ok(win, -16.31)

    def collect(db, company, base, start, after=None, news_limit=3, disclosure_limit=3):
        state["collect"].append({"company": company.name, "base": base, "start": start, "after": after,
                                 "news_limit": news_limit})
        return dict(state["found"])

    monkeypatch.setattr(answer_cause, "index_change", index_change)
    monkeypatch.setattr(answer_cause, "collect", collect)
    monkeypatch.setattr(agent, "_llm_chat", lambda **k: pytest.fail("기간 원인 질문은 채팅 LLM을 부르지 않는다"))
    monkeypatch.setattr(agent, "datetime", type("D", (datetime,), {"now": staticmethod(lambda tz=None: NOW if tz else NOW)}))
    agent._reset_llm_state()
    return state


def test_kakao_month_question_answers_on_code_path_with_separate_index(route):
    resp, records = agent._answer_forecast("카카오 한 달 동안 왜 떨어졌어?", None)
    text = resp["answer"]
    assert text.startswith("카카오(035720) · '한 달' 주가 움직임과 원인 · 분석 2026-10-16 종가 → 2026-11-12 종가")
    assert "카카오는 코스피" not in text and "코스피 지수: " in text
    assert [r["tool_name"] for r in records] == ["stock_tool", "market_tool", "news_tool", "disclosure_tool"]
    c = route["collect"][0]
    assert c["base"] == LAST and c["start"] == date(2026, 10, 16)
    assert c["after"] == answer_cause.cutoff_at(date(2026, 10, 16)) and c["news_limit"] == answer_cause.PERIOD_NEWS_LIMIT
    stock = records[0]["result"]
    assert stock["called_via"] == "cause_period" and stock["period_end_date"] == "2026-11-12"
    assert agent._meta()["cause_period"]["missing_tail"] == 4


def test_celltrion_last_week_surge_premise_is_corrected_on_code_path(route):
    route["companies"] = [CELL]
    route["index"] = None
    text = agent._answer_forecast("셀트리온이 지난주 급등한 이유가 뭐야?", None)[0]["answer"]
    assert "질문의 전제와 다릅니다: 질문은 '급등'이라고 하셨지만" in text and "시장 흐름과" not in text


def test_previous_company_is_used_for_subjectless_period_question_but_not_market(route):
    route["companies"] = []
    prev = {"report_id": 1, "question": "카카오 주가 알려줘", "answer": "", "company_name": "카카오"}
    assert agent._forecast_subject("최근 한 달 동안 왜 상승했어?", prev)[2] == "cause_period"
    assert agent._forecast_subject("최근 한 달 동안 코스피가 왜 떨어졌어?", prev) is None


def test_two_companies_unknown_company_and_event_questions_are_not_period_code_path(route):
    route["companies"] = [KAKAO, CELL]
    assert agent._forecast_subject("카카오랑 셀트리온 지난주 왜 떨어졌어?", None) is None
    route["companies"] = []
    assert agent._forecast_subject("삼송전자 지난주 왜 올랐어?", None) is None  # 종목을 못 찾으면 지어내지 않고 기존 흐름(되묻기 등)
    route["companies"] = [CELL]
    assert agent._forecast_subject("셀트리온 지난주 실적 발표 후 왜 떨어졌어?", None) is None


@pytest.mark.parametrize("q", ["삼송전자 지난주 왜 올랐어?", "현대 지난주 왜 떨어졌어?", "카카오뱅큰 한 달 동안 왜 빠졌어?",
                               "삼송전자 왜 올랐어?"])
def test_unresolved_or_ambiguous_name_never_falls_back_to_previous_company(route, q):
    # 질문이 종목 이름으로 시작하는데 못 찾았으면(오타·그룹명) 직전 종목(카카오)의 수치로 답하지 않는다(69·71차 경로 모두)
    route["companies"] = []
    prev = {"report_id": 1, "question": "카카오 주가 알려줘", "answer": "", "company_name": "카카오"}
    assert agent._forecast_subject(q, prev) is None


@pytest.mark.parametrize("q, ok", [("왜 떨어졌어?", True), ("오늘 주가가 하락한 원인이 뭐야?", True), ("최근 한 달 동안 왜 상승했어?", True),
                                   ("그럼 지난주엔 왜 올랐어?", True), ("이 종목 지난달 왜 빠졌어?", True), ("삼송전자 지난주 왜 올랐어?", False),
                                   ("현대 지난주 왜 떨어졌어?", False)])
def test_subjectless_question_shape(q, ok):
    assert answer_cause.is_subjectless(q) is ok


def test_future_period_answers_without_prices(route):
    route["companies"] = [SAMSUNG]
    resp, records = agent._answer_forecast("삼성전자 다음 주 왜 올랐어?", None)
    assert "아직 오지 않은 기간입니다" in resp["answer"] and route["collect"] == []
    assert records[0]["result"]["found"] is False


def test_etf_period_question_has_no_disclosure_record(route):
    route["companies"] = [KODEX]
    route["index"] = {"status": "no_index"}
    resp, records = agent._answer_forecast("KODEX 200 지난주 왜 빠졌어?", None)
    assert "disclosure_tool" not in [r["tool_name"] for r in records] and "market_tool" not in [r["tool_name"] for r in records]
    assert "ETF라 상장 시장 지수 정보가 없어" in resp["answer"]


# ---------- 6. 데이터 부족 안내(추가 조건 D): 환율·조사·ETF 표기 ----------

def test_stale_fx_answer_is_not_presented_as_current():
    from app.answer_basis import ensure_basis, fx_lag

    rec = [{"tool_name": "fx_tool", "result": {"found": True, "latest_date": "2026-11-02"}}]
    assert fx_lag(date(2026, 11, 2), NOW) == 11  # 11-03~11-17 평일(11-18 10시는 종가 전)
    out = ensure_basis("원달러 환율은 최근 5일간 하락했습니다. 현재 환율은 약 1,325.52원에 거래되고 있습니다.", rec, NOW)
    assert "현재 환율" not in out and "거래되고 있습니다" not in out
    assert "2026-11-02 종가 기준 환율은 약 1,325.52원입니다" in out and "11영업일 전 값이라 최신 환율이 아닙니다" in out


def test_fresh_fx_answer_only_gets_basis_date():
    from app.answer_basis import ensure_basis

    rec = [{"tool_name": "fx_tool", "result": {"found": True, "latest_date": "2026-11-17"}}]
    out = ensure_basis("원달러 환율은 1,400원입니다.", rec, NOW)
    assert "환율 기준일: 2026-11-17 종가" in out and "최신 환율이 아닙니다" not in out


@pytest.mark.parametrize("word, expected", [("0거래일", "'0거래일'은"), ("13월", "'13월'은"), ("다음 주", "'다음 주'는"),
                                            ("한 달", "'한 달'을"), ("지난주", "'지난주'를")])
def test_quoted_josa(word, expected):
    got = answer_cause._quote_topic(word) if expected.endswith(("은", "는")) else answer_cause._quote_obj(word)
    assert got == expected


def test_etf_line_says_etf_not_stock():
    q = "KODEX 200 지난주 왜 빠졌어?"
    text = answer(KODEX, q, window(q), {"status": "no_index"})
    assert "- KODEX 200(ETF) 주가:" in text and "(종목)" not in text


def test_short_history_analysis_line_is_consistent():
    rows = rows_until(start=date(2026, 10, 20))
    q = "카카오 지난달 왜 떨어졌어?"
    text = answer(KAKAO, q, answer_cause.period_window(spec(q), rows, NOW.date()))
    assert "저장 시세가 있는 2026-10-20 종가부터 2026-10-30 종가까지" in text and "기간 직전 거래일 2026-10-20" not in text
