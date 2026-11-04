"""전망·후속 결론 경로(app/outlook.py)와 용어 사전 회귀, 근거 뉴스 후처리(app/news_relevance.evidence_news). DB·LLM 없음."""

import statistics
from datetime import date, datetime, timedelta
from types import SimpleNamespace as NS

import pandas as pd
import pytest

from app import agent, outlook
from app.briefing import KST
from app.glossary import concept_answer
from app.news_relevance import evidence_news, is_duplicate_title


# ---------- 의도 분류 ----------

@pytest.mark.parametrize("q", ["SK하이닉스 다음 주 주가 전망", "SK하이닉스 일주일 안에 오를까?", "앞으로 주가가 어떻게 될 것 같아?",
                               "이번 주에 매수하는 게 위험할까?", "에코프로 지금 사도 될까?", "삼성SDI 지금 매수해도 될까?",
                               "네이버 주가 오를까 떨어질까", "한화에어로스페이스 사야 할까 팔아야 할까?"])
def test_forecast_questions(q):
    assert outlook.is_forecast_question(q)


@pytest.mark.parametrize("q", ["그래서 결론이 뭐야?", "올라 아니면 내려?", "네 판단으로는 상승이야 하락이야?", "니 생각에 오를거 같냐 내릴거 같냐",
                               "방금 분석한 결과에서 하락 가능성은 얼마나 돼?", "하락 위험은 어떤데?", "그럼 어떻게 해야 돼?", "그럼 사? 말아?"])
def test_followup_verdict_questions(q):
    assert outlook.is_followup_verdict_question(q)


@pytest.mark.parametrize("q,kind", [("방금 분석한 결과에서 하락 가능성은 얼마나 돼?", "probability"), ("하락 위험은 어떤데?", "risk"),
                                    ("그래서 결론이 뭐야?", "verdict"), ("니 생각에 오를거 같냐 내릴거 같냐", "verdict")])
def test_followup_kind(q, kind):
    assert outlook.followup_kind(q) == kind


@pytest.mark.parametrize("q", ["삼성전자 10주 사줘", "SK하이닉스 지금 바로 매수 주문 넣어줘", "LG전자 5주 매수해줘", "카카오 3주 팔아줘",
                               "삼성전자 왜 떨어졌어?", "SK하이닉스 실적 전망 뉴스 알려줘", "PER이 뭐야", "공매도 뜻 알려줘",
                               "그럼 최근 뉴스는?", "삼성전자 일주일 동안 얼마나 올랐어?"])
def test_not_forecast_or_followup(q):
    assert not outlook.is_forecast_question(q) and not outlook.is_followup_verdict_question(q)


def test_periods_like_one_week_are_not_orders():
    assert not outlook.is_order_request("SK하이닉스 1주일 안에 오를까?")
    assert not outlook.is_order_request("삼성전자 2주 뒤에 오를까?")
    assert outlook.is_order_request("삼성전자 2주 사줘")


@pytest.mark.parametrize("q,label", [("SK하이닉스 일주일 안에 오를까", "1주일"), ("다음 주 전망", "1주일"), ("한 달 안에 오를까", "1개월"),
                                     ("3개월 전망", "3개월"), ("석 달 뒤", "3개월"), ("전망 어때", None)])
def test_period_in_question(q, label):
    assert outlook.period_in(q) == label


def test_asks_subject_only_without_market_words():
    assert outlook.asks_subject("그래서 결론이 뭐야?")
    assert not outlook.asks_subject("코스피 앞으로 오를까?")
    assert not outlook.asks_subject("오를 종목 추천해줘")


# ---------- 변경 A: 용어 사전 ----------

@pytest.mark.parametrize("q", ["그래서 결론이 뭐야", "그래서 결론이 뭐야? 올라? 아니면 내려가?", "결론이 뭐야? 올라? 내려?",
                               "그래서 어떻게 해야 돼", "니 생각은 뭐야?", "그럼 판단이 뭐야?"])
def test_followup_is_not_a_definition_question(q):
    assert concept_answer(q) is None


@pytest.mark.parametrize("q,word", [("PER이 뭐야", "주당순이익"), ("공매도 뜻 알려줘", "빌려"), ("배당락이 무슨 뜻이야?", "배당기준일")])
def test_glossary_still_answers_terms(q, word):
    assert word in concept_answer(q)


def test_unknown_real_term_keeps_the_existing_notice():
    assert concept_answer("숏커버링이 뭐야?").startswith("'숏커버링'은(는) 내장 용어 사전에 없는")


# ---------- 지표 계산(독립 계산으로 검산) ----------

def _rows(n=260, seed=7):
    import random

    rnd = random.Random(seed)
    d, price, out = date(2025, 1, 2), 10000.0, []
    while len(out) < n:
        if d.weekday() < 5:
            price = max(100.0, round(price * (1 + rnd.uniform(-0.04, 0.04))))
            out.append((d, price, rnd.randint(1000, 5000)))
        d += timedelta(days=1)
    return out


def test_five_day_return_and_moving_averages():
    rows = _rows()
    ind = outlook.compute_indicators(rows, outlook.PERIODS["1주일"])
    closes = [c for _, c, _ in rows]
    assert ind["returns"][5]["pct"] == round((closes[-1] / closes[-6] - 1) * 100, 2)
    assert ind["returns"][5]["from_date"] == rows[-6][0].isoformat()
    assert ind["returns"][20]["pct"] == round((closes[-1] / closes[-21] - 1) * 100, 2)
    assert ind["ma5"]["value"] == round(sum(closes[-5:]) / 5, 2)
    assert ind["ma20"]["value"] == round(sum(closes[-20:]) / 20, 2)


def test_rsi_macd_volatility_match_independent_calculation():
    rows = _rows()
    closes = [c for _, c, _ in rows]
    ind = outlook.compute_indicators(rows, outlook.PERIODS["1개월"])
    diffs = pd.Series(closes).diff().iloc[-14:]
    gain, loss = diffs.clip(lower=0).mean(), (-diffs.clip(upper=0)).mean()
    assert ind["rsi"] == round(gain / (gain + loss) * 100, 2)
    s = pd.Series(closes)
    line = s.ewm(span=12, adjust=False).mean() - s.ewm(span=26, adjust=False).mean()
    assert ind["macd"]["macd"] == pytest.approx(round(float(line.iloc[-1]), 2), abs=0.011)
    assert ind["macd"]["signal"] == pytest.approx(round(float(line.ewm(span=9, adjust=False).mean().iloc[-1]), 2), abs=0.011)
    rets = [(b / a - 1) * 100 for a, b in zip(closes[-21:-1], closes[-20:])]
    assert ind["volatility"] == round(statistics.stdev(rets), 2)


def test_high_low_range_is_labeled_and_separate_from_returns():
    rows = _rows()
    ind = outlook.compute_indicators(rows, outlook.PERIODS["1주일"])
    window = [c for _, c, _ in rows[-21:]]
    assert ind["range"]["high"] == max(window) and ind["range"]["from_date"] == rows[-21][0].isoformat()
    assert ind["range"]["from_high_pct"] == round((rows[-1][1] / max(window) - 1) * 100, 2)
    lines = outlook.indicator_lines(ind, outlook.PERIODS["1주일"])
    assert any(line.startswith("- 최고 종가 대비:") and "조회 기간" in line for line in lines)


def test_same_input_gives_same_signals():
    rows = _rows()
    p = outlook.PERIODS["1주일"]
    a = outlook.classify_signals(outlook.compute_indicators(rows, p), p)
    b = outlook.classify_signals(outlook.compute_indicators(rows, p), p)
    assert a == b and {s["side"] for s in a} <= {"up", "down", "neutral"}


def _signals(n_up, n_down, n_neutral=0):
    return [{"side": "up"}] * n_up + [{"side": "down"}] * n_down + [{"side": "neutral"}] * n_neutral


@pytest.mark.parametrize("up,down,neutral,label", [(5, 2, 1, "상승 우위"), (1, 4, 3, "하락 우위"), (3, 2, 3, "중립"), (2, 2, 0, "판단 유보")])
def test_judge_by_indicators_before_groups(up, down, neutral, label):
    """묶음 적용 전 판정(지표마다 1표). 비교용으로 남긴 함수."""
    assert outlook.judge_by_indicators(_signals(up, down, neutral), {"hold": False}) == label


def _sig(kind, side):
    return {"kind": kind, "group": outlook.SIGNAL_GROUPS[kind], "side": side, "name": kind}


def test_correlated_indicators_count_as_one_vote():
    """같은 묶음(단기 흐름)의 지표 세 개가 모두 상승 쪽이어도 1표다. 묶음 적용 전에는 3표였다."""
    sig = [_sig("close_vs_ma_short", "up"), _sig("period_return", "up"), _sig("relative", "up"),
           _sig("close_vs_ma_long", "neutral"), _sig("rsi", "neutral"), _sig("macd", "neutral"), _sig("volume", "neutral")]
    assert outlook.group_votes(sig)["단기 흐름"] == "up"
    assert outlook.judge(sig, {"hold": False}) == "중립"
    assert outlook.judge_by_indicators(sig, {"hold": False}) == "상승 우위"


def test_group_vote_majority_and_tie():
    sig = [_sig("close_vs_ma_long", "up"), _sig("ma_alignment", "down"), _sig("close_vs_ma_short", "down"),
           _sig("period_return", "down"), _sig("relative", "up"), _sig("macd", "down"), _sig("rsi", "neutral")]
    votes = outlook.group_votes(sig)
    assert votes == {"중기 추세": "neutral", "단기 흐름": "down", "MACD": "down", "RSI": "neutral"}
    assert outlook.judge(sig, {"hold": False}) == "하락 우위"


def test_judge_holds_with_few_groups_or_bad_data():
    two_groups = [_sig("close_vs_ma_short", "up"), _sig("macd", "up")]
    assert outlook.judge(two_groups, {"hold": False}) == "판단 유보"
    many = [_sig(k, "up") for k in outlook.SIGNAL_GROUPS]
    assert outlook.judge(many, {"hold": False}) == "상승 우위"
    assert outlook.judge(many, {"hold": True}) == "판단 유보"


NOW = datetime(2026, 11, 3, 18, 0, tzinfo=KST)  # 화요일 장 마감 뒤


def test_data_check_warns_for_one_to_five_business_days():
    data = outlook.data_check(date(2026, 10, 30), NOW, [])
    assert data["lag_business_days"] == 2 and not data["hold"] and "2영업일 늦습니다" in data["warnings"][0]


def test_data_check_holds_when_more_than_five_business_days_late():
    data = outlook.data_check(date(2026, 10, 23), NOW, [])
    assert data["lag_business_days"] == 7 and data["hold"] and "판단을 유보" in data["warnings"][0]


def test_data_check_holds_when_trading_days_are_missing():
    data = outlook.data_check(date(2026, 11, 3), NOW, [date(2026, 10, 15)])
    assert data["hold"] and "2026-10-15" in data["warnings"][0]


def test_intraday_today_is_not_expected_yet():
    morning = datetime(2026, 11, 3, 10, 0, tzinfo=KST)
    assert outlook.expected_trading_day(morning) == date(2026, 11, 2)


def test_short_history_falls_back_to_hold():
    rows = _rows(n=8)
    p = outlook.PERIODS["3개월"]
    ind = outlook.compute_indicators(rows, p)
    sig = outlook.classify_signals(ind, p)
    assert len(sig) < outlook.MIN_SIGNAL_INDICATORS
    assert outlook.judge(sig, {"hold": False}) == "판단 유보"
    assert outlook.compute_indicators(rows[:1], p) is None


# ---------- 변경 C: 근거 뉴스 정합성 ----------

def _news(title, days_ago=1, source="s"):
    return {"title": title, "source": source, "published_at": (NOW - timedelta(days=days_ago)).isoformat()}


def test_skhynix_news_excludes_other_companies():
    items = [_news("삼성전자, HBM 공급 확대"), _news("삼성전기 실적 개선"), _news("SK하이닉스, HBM4 양산"),
             _news("반도체주 동반 강세")]
    kept = evidence_news("SK하이닉스", "stock", items)
    assert [k["title"] for k in kept] == ["SK하이닉스, HBM4 양산"]


def test_alias_counts_as_the_company():
    assert evidence_news("NAVER", "stock", [_news("네이버, 커머스 개편")])


def test_publication_date_filter():
    items = [_news("SK하이닉스 신고가", 3), _news("SK하이닉스 감산", 20)]
    kept = evidence_news("SK하이닉스", "stock", items, since=NOW - timedelta(days=14))
    assert [k["title"] for k in kept] == ["SK하이닉스 신고가"]


def test_duplicate_titles_from_several_outlets_are_merged():
    items = [_news("SK하이닉스, HBM4 수율 초과 달성", source="a"), _news("[단독] SK하이닉스 HBM4 수율 초과 달성", source="b")]
    assert is_duplicate_title(items[0]["title"], items[1]["title"])
    assert len(evidence_news("SK하이닉스", "stock", items)) == 1


def test_etf_news_needs_the_etf_name_in_the_title():
    items = [_news("KODEX 200 거래대금 1위"), _news("KODEX 200TR 신규 상장"), _news("ETF 분배금 제각각")]
    kept = evidence_news("KODEX 200", "etf", items, other_etf_names=["KODEX 200TR"])
    assert [k["title"] for k in kept] == ["KODEX 200 거래대금 1위"]


def test_target_price_titles_are_not_quoted():
    assert outlook.filter_titles([{"title": "SK하이닉스 목표가 상향"}, {"title": "SK하이닉스 신고가"}]) == [{"title": "SK하이닉스 신고가"}]


# ---------- 답변 문장 ----------

COMPANY = NS(id=20, name="SK하이닉스", ticker="000660", market="KOSPI", asset_type="stock")


def _result(rows=None, period="1주일", hold=False):
    p = outlook.PERIODS[period]
    ind = outlook.compute_indicators(rows or _rows(), p)
    sig = outlook.classify_signals(ind, p)
    data = outlook.data_check(date.fromisoformat(ind["base_date"]), NOW, [date(2026, 1, 5)] if hold else [])
    return {"indicators": ind, "signals": sig, "label": outlook.judge(sig, data), "data": data}


def test_forecast_answer_sections_and_marker():
    res = _result()
    text = outlook.forecast_answer(COMPANY, outlook.PERIODS["1주일"], res, [], [], None, NOW)
    titles = [line[3:] for line in text.splitlines() if line.startswith("## ")]
    # 2026-11-04: 결론 먼저(제목 줄·상태 줄은 섹션 앞 문단), 판정 규칙은 "신호 판정 상세"에만(tests/test_outlook_round23.py)
    assert titles == ["종합 판단", "상승을 뒷받침하는 근거", "하락 위험 근거", "앞으로 확인할 사항", "과거 검증", "기술적 지표",
                      "뉴스·공시(직접 관련)", "시장 상황", "신호 판정 상세", "불확실성과 한계", "데이터 기준일·출처"]
    assert outlook.marker_of(text) == {"name": "SK하이닉스", "ticker": "000660", "period": "1주일"}
    assert "관련 뉴스를 찾지 못했습니다" in text and "원/달러 환율: 데이터 없음" in text
    assert "묶음 판정(묶음당 1표)" in text and "과거 검증" in text
    assert "기준일(최신 거래일)" in text and "수익률 계산식" in text
    # 영어 문장 단어("fluctuate" 같은)가 섞이지 않는다: 영문은 지표·출처 식별자와 면책 문구의 "AI"뿐이다
    import re

    allowed = {"SK", "RSI", "MACD", "DART", "DB", "AI", "N", "Asia", "Seoul", "stock", "price", "market", "index", "exchange",
               "rate", "news", "disclosure", "quarterly", "earnings", "app", "data", "outlook", "backtest", "json"}
    assert set(re.findall(r"[A-Za-z]+", text)) <= allowed


def test_forecast_answer_has_no_assertions_or_probabilities():
    from evals.forecast_check import assertion_hits
    from evals.metrics import forbidden_hits

    text = outlook.forecast_answer(COMPANY, outlook.PERIODS["1주일"], _result(), [], [], None, NOW)
    assert assertion_hits(text) == [] and forbidden_hits(text) == []


def test_etf_without_news_omits_the_section_and_keeps_leverage_notice():
    etf = NS(id=318, name="KODEX 레버리지", ticker="122630", market=None, asset_type="etf")
    text = outlook.forecast_answer(etf, outlook.PERIODS["1주일"], _result(), [], [], None, NOW)
    assert "## 뉴스·공시" not in text and "레버리지 ETF" in text


def test_hold_answer_states_the_reason():
    text = outlook.forecast_answer(COMPANY, outlook.PERIODS["1주일"], _result(hold=True), [], [], None, NOW)
    assert "판단 유보" in text.splitlines()[2] and "판단을 유보합니다" in text and "데이터 경고:" in text


def test_risk_followup_shows_risks_and_support_levels():
    text = outlook.followup_answer(COMPANY, outlook.PERIODS["1주일"], _result(), "risk", NOW)
    assert "하락 위험:" in text and "분석 기간 최저 종가" in text and "52주 최저 종가" in text and "## " not in text


def test_probability_followup_says_no_verified_probability():
    text = outlook.followup_answer(COMPANY, outlook.PERIODS["1주일"], _result(), "probability", NOW)
    assert "검증된 상승·하락 확률은 없습니다." in text


# ---------- agent 연결(LLM 미사용, 직전 종목·기간 유지) ----------

@pytest.fixture
def wired(monkeypatch):
    """DB 대신 가짜 종목·지표를 넣고, LLM을 부르면 실패하게 한다(Ollama 연결 실패와 같은 상황)."""
    def no_llm(**kw):
        raise ConnectionError("Failed to connect to Ollama")

    monkeypatch.setattr(agent, "_LIBRARY_CHAT", no_llm)
    monkeypatch.setattr(agent, "_llm_chat", lambda **kw: (_ for _ in ()).throw(AssertionError("LLM을 부르면 안 된다")))

    class Db:
        def query(self, *a):
            return self

        def filter(self, *a):
            return self

        def first(self):
            return COMPANY

        def close(self):
            pass

    monkeypatch.setattr(agent, "SessionLocal", lambda: Db())
    monkeypatch.setattr(agent, "_question_companies", lambda q: [COMPANY] if "하이닉스" in q else [])
    seen = []

    def analyze(db, company, period, now):
        seen.append((company.name, period.label))
        return _result(period=period.label)

    monkeypatch.setattr(outlook, "analyze", analyze)
    monkeypatch.setattr(outlook, "evidence", lambda db, c, p, now: ([], []))
    monkeypatch.setattr(outlook, "fx_summary", lambda db, p, d: None)
    monkeypatch.setattr(agent.related_news, "indirect_news", lambda db, c, since, exclude_urls=(): (None, None, []))
    monkeypatch.setattr(agent.earnings, "recent_for_answer", lambda db, c: None)
    monkeypatch.setattr(agent, "_answer_briefing", lambda q, u: None)
    return seen


def test_forecast_path_answers_without_llm(wired):
    resp, records = agent._answer("SK하이닉스 한 달 안에 오를까?", "m")
    assert resp["answer"].startswith("SK하이닉스(000660) · 향후 20거래일\n")
    assert resp["used_tools"][0] == "stock_tool" and records[0]["result"]["period"] == "1개월"
    assert wired == [("SK하이닉스", "1개월")]


def test_followup_keeps_previous_company_and_period(wired):
    first, _ = agent._answer("SK하이닉스 한 달 안에 오를까?", "m")
    previous = {"question": "SK하이닉스 한 달 안에 오를까?", "answer": first["answer"], "company_name": "SK하이닉스"}
    second, _ = agent._answer("그래서 결론이 뭐야? 올라? 아니면 내려가?", "m", previous)
    assert outlook.marker_of(second["answer"]) == {"name": "SK하이닉스", "ticker": "000660", "period": "1개월"}
    third_prev = {"question": "그래서 결론이 뭐야? 올라? 아니면 내려가?", "answer": second["answer"], "company_name": "SK하이닉스"}
    third, _ = agent._answer("아니 니 생각에 오를거 같냐 내릴거 같냐", "m", third_prev)
    assert outlook.marker_of(third["answer"])["period"] == "1개월" and "용어 사전" not in third["answer"]
    assert wired == [("SK하이닉스", "1개월")] * 3


def test_followup_without_previous_asks_which_company(wired):
    resp, records = agent._answer("그래서 결론이 뭐야?", "m")
    assert resp["answer"] == outlook.ASK_SUBJECT_ANSWER and records == []
