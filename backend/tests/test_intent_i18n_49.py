"""49차 문제 2: 영어·일본어·중국어 질문의 기간·전망 판정(app/intent_i18n.py, app/period_parser.py, app/outlook.horizon_in).

수정 전 실측(2026-11-13, 코드 판정만):
- 한국어도 "최근 5거래일"을 기간으로 읽지 못했다(parse_period None -> LLM이 기간 추측).
- "향후 20거래일 전망"은 기본 1주일(5거래일)로, "향후 1년 전망"도 말없이 1주일로 답했다(기간 숫자가 바뀜).
- 영어·일본어·중국어 질문은 기간·전망 판정이 모두 False/None이었다("起亚未来5个交易日的走势预测"도 전망 경로가 아니었다).
아래 문장은 49차 신규 평가 문항(evals/multilingual_cases_49.txt)과 겹치지 않게 따로 만든 것이다.
"""

import pytest

from app import agent, answer_i18n, outlook, price_flow
from app.intent_i18n import is_foreign_question, normalize
from app.period_parser import parse_period


def route(question: str) -> dict:
    """에이전트가 쓰는 판정 함수들의 결과(정규화한 질문 기준)."""
    t = normalize(question).text
    p = parse_period(t)
    h = outlook.horizon_in(t)
    forecast = outlook.is_forecast_question(t)
    return {"forecast": forecast,
            "future_days": (outlook.PERIODS[h.label].days if h and h.label else None) if h else
                           (outlook.PERIODS[outlook.period_in(t)].days if forecast and outlook.period_in(t) else None),
            "unsupported": bool(h and h.unsupported),
            "past_days": p.period_days if p and not forecast else None,
            "flow": price_flow.is_price_flow_question(t)}


# ---------- 한국어 기반(정규화가 옮겨 갈 표준 표현) ----------

class TestKoreanCore:
    @pytest.mark.parametrize("q, n", [("기아 최근 5거래일 주가", 5), ("기아 지난 20영업일 흐름", 20), ("최근 60거래일 수익률", 60)])
    def test_trading_days_are_exact(self, q, n):
        p = parse_period(q)
        assert p is not None and p.period_days == n

    @pytest.mark.parametrize("q", ["기아 향후 5거래일 전망", "기아 5거래일 뒤 주가", "기아 2026-03-02 이후 5거래일"])
    def test_future_or_anchored_counts_are_not_past_windows(self, q):
        p = parse_period(q)
        assert p is None or p.matched_text != "5거래일"

    @pytest.mark.parametrize("q, label, note", [
        ("기아 향후 5거래일 전망", "1주일", False), ("기아 향후 20거래일 전망", "1개월", False), ("기아 향후 60거래일 전망", "3개월", False),
        ("기아 향후 10거래일 전망", "1주일", True), ("기아 3주 뒤 오를까", "1개월", True), ("기아 4주 뒤 오를까", "1개월", False)])
    def test_horizon_mapping_keeps_or_announces_the_number(self, q, label, note):
        h = outlook.horizon_in(q)
        assert (h.label, h.note) == (label, note)
        if note:
            assert f"약 {h.requested_days}거래일" in outlook.horizon_note(h) and "가장 가까운" in outlook.horizon_note(h)

    @pytest.mark.parametrize("q", ["기아 향후 1년 전망", "기아 내년 전망", "기아 향후 6개월 전망"])
    def test_unsupported_horizon(self, q):
        h = outlook.horizon_in(q)
        assert h is not None and h.unsupported
        assert "제공하지 않습니다" in outlook.unsupported_horizon_answer(h)

    def test_unsupported_horizon_calls_no_tool(self, monkeypatch):
        class Co:
            id, name, ticker, asset_type = 1, "기아", "000270", "stock"
        agent._reset_llm_state()
        monkeypatch.setattr(agent, "_forecast_subject", lambda q, prev: (Co(), None, "brief"))
        monkeypatch.setattr(agent, "SessionLocal", lambda: pytest.fail("DB·도구를 부르면 안 된다"))
        resp, records = agent._answer_forecast("기아 향후 1년 전망", None)
        assert records == [] and resp["used_tools"] == [] and "5·20·60거래일" in resp["answer"]


# ---------- 다국어 정규화 ----------

PAST_FUTURE = [
    # (과거 질문, 미래 질문, N)
    ("Analyze Kia stock over the last 5 trading days", "What is the outlook for Kia over the next 5 trading days?", 5),
    ("起亜の直近5営業日の株価を分析して", "起亜の今後5営業日の株価見通しは？", 5),
    ("起亚最近5个交易日的股价分析", "起亚未来5个交易日的走势预测", 5),
    ("Analyze Kia over the past 20 trading days", "Kia forecast for the next 20 trading days", 20),
    ("起亜の過去20営業日の株価推移", "起亜の今後20営業日の見通し", 20),
    ("起亚过去20个交易日的走势", "起亚未来20个交易日的前景", 20),
]


@pytest.mark.parametrize("past, future, n", PAST_FUTURE)
def test_past_and_future_windows_take_different_paths_with_the_same_number(past, future, n):
    a, b = route(past), route(future)
    assert (a["forecast"], a["past_days"]) == (False, n), a
    assert (b["forecast"], b["future_days"]) == (True, n), b


MONTH = "month"  # "1개월"은 실행일 기준 달력 한 달의 평일 수라 날마다 다르다(2026-11-13엔 24, 11-14엔 23). 51차에 고정값을 없앴다


@pytest.mark.parametrize("q, days", [
    ("How did Kia shares move over the past month?", MONTH), ("起亚过去一个月股价走势", MONTH), ("起亜の過去1か月の株価推移", MONTH),
    ("Kia stock price last week", 5), ("起亜の先週の株価", 5), ("起亚上周的股价", 5),
])
def test_past_calendar_periods(q, days):
    if days == MONTH:  # 기대값 = 같은 날 한국어 표준 표현 "1개월"의 서비스 정의
        days = parse_period("기아 최근 1개월 주가").period_days
        assert 19 <= days <= 24, days  # 정의가 깨져 엉뚱한 값이 나오는 것만 막는다
    r = route(q)
    assert r["forecast"] is False and r["past_days"] == days, r


@pytest.mark.parametrize("q, days", [
    ("Will Kia go up next month?", 20), ("起亜の株価は来月上がりますか？", 20), ("起亚下个月会涨吗？", 20),
    ("Will Kia fall next week?", 5), ("起亜は来週下がりますか", 5), ("起亚下周会跌吗", 5),
])
def test_relative_future_periods(q, days):
    r = route(q)
    assert r["forecast"] is True and r["future_days"] == days, r


@pytest.mark.parametrize("q", ["Kia outlook for next year", "起亜の来年の見通し", "起亚明年的前景", "Kia forecast for the next 6 months"])
def test_unsupported_future_periods_are_flagged(q):
    assert route(q)["unsupported"] is True


@pytest.mark.parametrize("q, windows", [
    ("Show Kia's 5-day and 20-day moving averages and returns", "5일·20일 이동평균"),
    ("起亜の5日・20日移動平均と騰落率", "5일·20일 이동평균"),
    ("起亚的5日和20日均线以及收益率", "5일·20일 이동평균"),
])
def test_moving_average_windows(q, windows):
    assert windows in normalize(q).text and route(q)["flow"] is True


@pytest.mark.parametrize("q, names", [
    ("Compare Kia and Hyundai Motor over the last 20 trading days", ("기아", "현대차")),
    ("起亜と現代自動車の最近20営業日の株価を比較して", ("기아", "현대차")),
    ("比较起亚和现代汽车最近20个交易日的股价", ("기아", "현대차")),
])
def test_comparison_keeps_both_names_and_the_window(q, names):
    t = normalize(q).text
    assert all(n in t for n in names) and "비교" in t and "최근 20거래일" in t


@pytest.mark.parametrize("q", ["기아 최근 5거래일 주가", "기아の株価", "KODEX 200 구성종목 알려줘", "삼성전자 vs SK하이닉스 비교"])
def test_korean_questions_are_not_touched(q):
    # 종목명 밖에 한글이 있으면 정규화하지 않는다("기아の株価"는 한글이 종목명뿐이라 정규화 대상)
    n = normalize(q, ["기아", "KODEX 200", "삼성전자", "SK하이닉스"])
    assert (n.changed is False) == (q != "기아の株価")


@pytest.mark.parametrize("q", ["How is Samsung stock doing?", "三星最近股价怎么样？", "現代の株価は？"])
def test_ambiguous_names_stay_ambiguous(q):
    t = normalize(q).text
    for registered in ("삼성전자", "삼성물산", "현대차", "현대모비스"):
        assert registered not in t


def test_holdings_wording_is_kept_for_the_etf_gate():
    from app.etf import asks_constituents

    for q in ("What are the top holdings of KODEX 200?", "What stocks are in TIGER 200?", "KODEX 200の構成銘柄は？"):
        assert asks_constituents(normalize(q).text)


def test_foreign_question_detection():
    assert is_foreign_question("Analyze Kia")
    assert is_foreign_question("기아の株価", ["기아"])
    assert not is_foreign_question("기아 주가 알려줘", ["기아"])
    assert not is_foreign_question("KODEX 200", ["KODEX 200"])


def test_ask_question_routes_with_the_normalized_text_but_saves_the_original(monkeypatch):
    seen, saved = {}, {}
    monkeypatch.setattr(answer_i18n, "_company_names", lambda: ("기아",))

    def fake_answer(q, model, previous=None):
        seen["q"] = q
        return {"answer": "기아의 2026-11-12 종가는 93,600원입니다.", "used_tools": [], "sources": []}, []

    monkeypatch.setattr(agent, "_answer", fake_answer)
    monkeypatch.setattr(agent, "save_report_row", lambda q, *a, **k: saved.setdefault("q", q) and 1)
    r = agent.ask_question("起亚未来5个交易日的走势预测", save_report=True)
    assert seen["q"].startswith("기아 향후 5거래일") and saved["q"] == "起亚未来5个交易日的走势预测"
    assert r["meta"]["question_routed"] == seen["q"] and "period:zh_future_window" in r["meta"]["routing_steps"]


def test_horizon_note_is_localized_and_the_rest_keeps_its_path():
    note = "요청하신 기간(향후 10거래일, 약 10거래일)은 지원하는 전망 기간(향후 5·20·60거래일)이 아니어서 가장 가까운 향후 5거래일(1주일) 기준으로 분석했습니다."
    horizon = {"kind": "note", "text": "향후 10거래일", "requested_days": 10, "label": "1주일", "note": note}
    out, status = answer_i18n.localize_answer("q", note + "\n\n본문", [], "en-US", horizon=horizon,
                                              fixed={"fallback_answer": "본문"})
    assert out.startswith("The requested period (about 10 trading days)") and "next 5 trading days" in out and status == "localized"


def test_n_trading_days_window_is_the_last_n_stored_days(monkeypatch):
    """49차 신규 평가에서 찾은 오류(C01~C03): "최근 20거래일" 비교가 오늘까지의 날짜 창이라, 오늘 종가가 아직 없으면 19거래일만 비교했다.
    거래일 수로 말한 기간은 저장된 최근 N개 거래일(장중 행 제외)이 창이고, 그 직전 거래일 종가가 기준이다."""
    from datetime import date, timedelta
    from types import SimpleNamespace

    days, d = [], date(2026, 11, 12)
    while len(days) < 30:
        if d.weekday() < 5:
            days.append(d)
        d -= timedelta(days=1)
    rows = [SimpleNamespace(price_date=x, close_price=100.0 + i, volume=10, change_pct=0.1) for i, x in enumerate(days)]

    class Co:
        id, name, ticker, asset_type = 1, "기아", "000270", "stock"

    monkeypatch.setattr(agent, "_load_price_rows", lambda company, limit: rows[:limit])
    r = agent._price_window(Co(), 20, date(2026, 10, 19), None, trading_days=20)
    assert r["trading_days"] == 20 and r["period_start_date"] == days[20].isoformat() and r["period_end_date"] == "2026-11-12"
    assert agent._TRADING_DAYS_TEXT_RE.match("20거래일") and not agent._TRADING_DAYS_TEXT_RE.match("한 달")


# ---------- 재확인에서 찾은 것(신규 문항을 본 뒤 고침) ----------

@pytest.mark.parametrize("q", ["How is Samsung stock doing?", "三星最近股价怎么样？", "サムスンの株価は？", "現代の株価は？", "삼성 주가 어때?",
                               "Hyundai outlook next month", "롯데 주가 알려줘"])
def test_ambiguous_group_name_asks_which_company_without_the_llm(monkeypatch, q):
    """재확인 R01: "How is Samsung stock doing?"에 모델이 도구 인자로 "삼성전자"를 골랐다(종목 해석은 거절했는데 모델이 등록명을 직접 씀).
    모호한 그룹 이름만 있고 특정 종목이 없으면 코드가 후보를 보여 주며 되묻는다(LLM·도구 호출 없음)."""
    monkeypatch.setattr(agent, "_question_companies", lambda question: [])
    monkeypatch.setattr(agent, "_first_call", lambda *a, **k: pytest.fail("LLM을 부르면 안 된다"))
    agent._reset_llm_state()
    resp, records = agent._answer(q, "m")
    assert records == [] and resp["used_tools"] == [] and "특정할 수 없습니다" in resp["answer"]


def test_specific_company_with_group_word_is_not_ambiguous(monkeypatch):
    class Co:
        id, name, ticker, asset_type = 1, "삼성전자", "005930", "stock"
    assert agent._ambiguous_group_answer("삼성전자 주가 어때?", [Co()]) is None
    assert agent._ambiguous_group_answer("Samsung Electronics stock", [Co()]) is None


@pytest.mark.parametrize("q", ["What's going on with HD현대중공업 stock and recent news?", "HD현대중공업 주가와 뉴스 분석"])
def test_stock_and_news_question_is_an_analysis_question(q):
    from app.routing import is_analysis_question
    assert is_analysis_question(q)


def test_ambiguous_answer_is_localized():
    text = agent.ambiguous_group_text("Samsung", ["삼성전자", "삼성물산"])
    out, status = answer_i18n.localize_answer("q", text, [], "en-US", ambiguous={"token": "Samsung", "candidates": ["삼성전자", "삼성물산"]})
    assert status == "localized" and "matches several listed companies (삼성전자, 삼성물산" in out
