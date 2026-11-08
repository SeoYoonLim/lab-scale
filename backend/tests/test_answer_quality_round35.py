"""35차(2026-11-07) AI 답변 품질 실전 개선의 회귀 테스트. DB·Ollama 없이 돈다(도구 결과는 합성).

33차 live 평가에서 나온 문제: "지금 사야 해?"가 관련 없는 뉴스 나열, 목표주가 요청이 시세만 답함, 단순 시세·주문 참고 시세에 지연 안내 없음,
번역 실패 시 한국어 원문 노출, 1414000.0원 표기, 같은 문단 반복, "거래량 많은 종목"이 탐색 경로가 아님, 기간 최고·최저 정의가 경로마다 다름.
"""

import re
from datetime import date, datetime

import pytest

from app import agent, answer_i18n, answer_tidy, market_overview, market_ranking, outlook, price_flow as pf, quote_freshness, quote_i18n
from app import request_safety
from app.briefing import KST
from app.i18n import disclaimer, text
from evals import quality_judges as J

SAT = datetime(2026, 11, 7, 15, 0, tzinfo=KST)    # 토요일: 기대 거래일 11-06(금)
FRI_EVE = datetime(2026, 11, 6, 18, 0, tzinfo=KST)
FRI_AM = datetime(2026, 11, 6, 10, 0, tzinfo=KST)  # 장중: 기대 거래일 11-05


def stock_result(name="가나다전자", d="2026-11-03", close=222000.0, pct=-3.48, vol=13338444, via=None):
    r = {"found": True, "company_name": name, "change_pct": pct, "volume": vol,
         "prices": [{"price_date": d, "close_price": close, "change_pct": pct, "volume": vol}]}
    if via:
        r["called_via"] = via
    return r


class _Co:
    def __init__(self, name="가나다전자", ticker="123450", asset_type="stock"):
        self.name, self.ticker, self.asset_type, self.market, self.id = name, ticker, asset_type, "KOSPI", 1


@pytest.fixture(autouse=True)
def _no_price_status_db(monkeypatch):
    """지연 이유 조회(app.outlook.price_status)는 DB를 읽는다. 순수 테스트에서는 없음으로 고정하고, 필요한 테스트만 바꾼다."""
    monkeypatch.setattr(agent, "_quote_price_status", lambda base, now: None)


# ---------- 1. 매수 판단 질문 -> 전망 경로 ----------

@pytest.mark.parametrize("q", ["삼성전자 지금 사야 해?", "삼성전자 사야 할까?", "삼성전자 팔아야 돼?", "삼성전자 매수해야 하나?"])
def test_buy_decision_questions_go_to_forecast(q):
    assert outlook.is_forecast_question(q) and not outlook.is_order_command(q)
    assert request_safety.classify(q, True) is None


@pytest.mark.parametrize("q,forecast", [
    ("SK하이닉스 앞으로 어떻게 될까?", True), ("삼성전자 오늘 주가 흐름 알려줘.", False), ("삼성전자 최근 뉴스 알려줘.", False),
    ("삼성전자 공시 확인해 줘.", False), ("삼성전자 20일 수익률 분석해 줘.", False), ("주식은 언제 사는 게 좋아?", False),
])
def test_routing_regression_questions(q, forecast):
    assert outlook.is_forecast_question(q) is forecast


def test_order_rules_unchanged():
    """주문 인식은 그대로(채팅은 주문하지 않는다). 매수 판단 질문을 전망으로 넓혀도 주문 요청은 주문 안내다."""
    for q in ("삼성전자 10주 사줘", "SK하이닉스 5주 매도해줘", "삼성전자 시장가로 사줘"):
        assert outlook.is_order_command(q), q
    for q in ("삼성전자 사야 합니다", "삼성전자 주가 알려줘"):
        assert not outlook.is_order_command(q) and not outlook.is_forecast_question(q), q


def test_strict_matching_unchanged():
    """주문 엄격 매칭(allow_fuzzy=False)은 그대로: 음차·오타는 받지 않는다."""
    from app.tools.company_resolver import resolve_company
    from evals.run_quality import seed_db

    db = seed_db()
    assert resolve_company(db, "엘지전자", allow_fuzzy=False).company is None
    assert resolve_company(db, "삼성전자", allow_fuzzy=False).company.name == "삼성전자"
    assert resolve_company(db, "엘지전자", allow_fuzzy=True).company.name == "LG전자"


def test_single_window_return_analysis_uses_trading_days():
    q = "삼성전자 20일 수익률 분석해 줘."
    assert pf.is_price_flow_question(q) and pf.windows(q)["returns"] == (20,)
    assert not pf.is_price_flow_question("삼성전자 최근 20일 수익률은?")  # 기간 질문(달력)은 기존 경로


# ---------- 2·3. 목표주가·가격 단정·보장 ----------

@pytest.mark.parametrize("q,company,kind", [
    ("삼성전자 목표주가 확정해 줘.", True, "target_price"),
    ("SK하이닉스 내일 얼마까지 올라?", True, "price_prediction"),
    ("한 달 뒤 주가를 정확하게 알려줘.", False, "price_prediction"),
    ("이 종목 수익률 20% 보장 가능해?", False, "guarantee"),
    ("이 주식 무조건 오르지?", False, "guarantee"),
    ("주식은 언제 사는 게 좋아?", False, "timing_general"),
    ("목표주가가 뭐야?", False, None),
    ("삼성전자 다음 주 주가 전망 알려줘", True, None),
    ("삼성전자 다음 주에 오를까?", True, None),
])
def test_request_safety_classification(q, company, kind):
    assert request_safety.classify(q, company) == kind


def test_each_kind_has_a_different_reason_and_no_invented_numbers():
    heads = {k: request_safety.answer(k, "가나다전자").split("\n\n")[0] for k in ("target_price", "price_prediction", "guarantee")}
    assert len(set(heads.values())) == 3  # 같은 문장으로 거절하지 않는다
    for k in ("target_price", "price_prediction", "guarantee", "timing_general"):
        a = request_safety.answer(k, None)
        assert J.check_safety(a, require_disclaimer=True) == [], k
        assert not re.search(r"\d+\s*(?:원|%)", a.split(disclaimer("ko-KR"))[0].replace("20거래일", "")), k
        assert J.check_required([], a, ["re:확정할 수 없|예측할 수 없|보장할 수 없|정해 드릴 수는 없"]) == [], k


def test_target_price_request_answers_with_reference_quote(monkeypatch):
    monkeypatch.setattr(agent, "_question_companies", lambda q: [_Co()])
    monkeypatch.setattr(agent, "_run_tool", lambda name, args: stock_result())
    resp, records = agent._answer_request_safety("가나다전자 목표주가 확정해 줘")
    a = resp["answer"]
    assert a.startswith("목표주가는 확정할 수 없습니다") and "참고 시세: 가나다전자의 2026-11-03 종가는 222,000원" in a
    assert "데이터 기준일: 2026-11-03" in a and a.rstrip().endswith(disclaimer("ko-KR"))
    assert J.check_safety(a, True) == [] and records[0]["result"]["called_via"] == "request_safety"


# ---------- 4~6. 시세 기준일·지연 ----------

def test_freshness_line_states_stored_close_and_lag():
    fresh = quote_freshness.line(date(2026, 11, 6), FRI_EVE)
    assert "2026-11-06 종가" in fresh and "실시간 시세가 아닙니다" in fresh and "지연" not in fresh
    one = quote_freshness.line(date(2026, 11, 5), FRI_EVE)
    assert "1영업일 이전" in one
    stale = quote_freshness.line(date(2026, 11, 3), SAT)
    assert "기대 거래일(2026-11-06)보다 3영업일 지연" in stale and "최신 시세가 아닙니다" in stale
    assert J.check_freshness(stale, date(2026, 11, 3), 3) == []  # 판정기 기준으로도 지연을 밝힘
    assert J.check_freshness(fresh, date(2026, 11, 6), 0) == []


def test_weekend_and_intraday_are_not_delays():
    assert quote_freshness.status(date(2026, 11, 6), SAT)["lag"] == 0
    assert quote_freshness.status(date(2026, 11, 5), FRI_AM)["lag"] == 0  # 장중에는 전일 종가가 최신


@pytest.mark.parametrize("locale", ["en-US", "ja-JP", "zh-CN"])
def test_freshness_line_locales_keep_values(locale):
    t = quote_freshness.line(date(2026, 11, 3), SAT, locale)
    assert "2026-11-03" in t and "2026-11-06" in t and "3" in t and not re.search(r"[가-힣]", t)


def test_simple_quote_always_has_base_date(monkeypatch):
    monkeypatch.setattr(agent, "_run_tool", lambda name, args: stock_result())
    resp, records = agent._answer_simple_quote("가나다전자 종가 알려줘", [_Co()])
    a = resp["answer"]
    assert a.startswith("가나다전자의 2026-11-03 종가는 222,000원입니다.")
    assert "데이터 기준일: 2026-11-03 종가(DB에 저장된 종가이며 실시간 시세가 아닙니다)" in a
    assert "마지막 데이터는" not in a  # 예전 안내와 겹치지 않는다


def test_order_guide_reference_quote_has_lag_notice_and_order_text_unchanged(monkeypatch):
    monkeypatch.setattr(agent, "_question_companies", lambda q: [_Co()])
    monkeypatch.setattr(agent, "_run_tool", lambda name, args: stock_result())
    resp, records = agent._answer_order_command("가나다전자 10주 사줘")
    a = resp["answer"]
    assert a.startswith(outlook.ORDER_GUIDE_ANSWER) and "참고 시세: 가나다전자의 2026-11-03 종가는 222,000원" in a
    assert "데이터 기준일: 2026-11-03" in a and records[0]["result"]["called_via"] == "order_command"


def test_missing_price_has_no_base_line():
    assert quote_freshness.base_of({"found": False}) is None
    assert quote_freshness.base_of(stock_result()) == date(2026, 11, 3)


def test_question_without_company_is_not_answered_with_an_invented_stock():
    resp, records = agent._answer_missing_subject("오늘 주가가 얼마야?", [])
    assert "종목 이름이나 종목코드" in resp["answer"] and records == []
    assert agent._answer_missing_subject("지금 시장 상황이 어때?", []) is None
    assert agent._answer_missing_subject("삼성전자 주가 얼마야?", [_Co()]) is None
    assert agent._answer_missing_subject("지금 주가 얼마야?", [])[0]["answer"].startswith("어떤 종목의 주가")
    # 질문 추출이 못 찾은 오타·음차는 되묻지 않고 기존 흐름(리졸버 후보 안내)으로 보낸다
    for q in ("삼성잔자 주가 알려줘", "삼전 주가 얼마야?", "삼성전자 주가 알려줘"):
        assert agent._answer_missing_subject(q, []) is None, q


def test_market_overview_question_detection():
    assert market_overview.is_overview_question("지금 시장 상황이 어때?")
    assert market_overview.is_overview_question("오늘 코스피 지수 어때?")
    assert not market_overview.is_overview_question("코스피가 뭐야?")
    assert not market_overview.is_overview_question("삼성전자 코스피 대비 어때?")
    assert not market_overview.is_overview_question("코스닥 전망 어때?")


def test_market_overview_answer():
    data = {"indexes": {"KS11": {"date": date(2026, 11, 3), "close": 5637.63, "returns": {1: -3.78, 5: -0.01, 20: -19.51}}},
            "breadth": {"date": date(2026, 11, 3), "up": 67, "down": 230, "flat": 3, "total": 300}}
    a = market_overview.answer(data, SAT)
    assert "코스피: 2026-11-03 종가 5,637.63 (1거래일 -3.78%, 5거래일 -0.01%, 20거래일 -19.51%)" in a
    assert "상승 67, 하락 230" in a and "3영업일 지연" in a and J.check_safety(a) == []


# ---------- 9·10. 번역 폴백·4개 언어 숫자 ----------

def test_translation_failure_shows_localized_data_not_korean(monkeypatch):
    from app import i18n_translate as tr

    monkeypatch.setattr(tr, "_chat", lambda m: (_ for _ in ()).throw(ConnectionError("down")))
    records = [{"tool_name": "stock_tool", "result": stock_result()},
               {"tool_name": "news_tool", "result": {"found": True, "news": [{"title": "가나다전자, 신공장 착공", "published_at": "2026-11-02T09:00:00"}]}}]
    korean = "가나다전자는 최근 신공장 착공 소식이 있었고 주가는 222,000원입니다. 투자에 유의하세요."
    for loc in ("en-US", "ja-JP", "zh-CN"):
        out, status = answer_i18n.localize_answer("가나다전자 어때?", korean, records, loc)
        assert status == answer_i18n.STATUS_FALLBACK_SUMMARY
        assert out.startswith(text("translation_fallback_summary", loc))
        assert "2026-11-03" in out and "222,000" in out and "13,338,444" in out and "-3.48%" in out
        assert korean not in out
        rest = out.replace("가나다전자", "").replace("신공장 착공", "")
        assert not re.search(r"[가-힣]", rest), (loc, rest)  # 종목명·기사 제목(원문 정책) 밖에 한국어 없음
        assert J.check_language(out, loc, ["가나다전자", "가나다전자, 신공장 착공"]) == []


def test_translation_failure_without_data(monkeypatch):
    from app import i18n_translate as tr

    monkeypatch.setattr(tr, "_chat", lambda m: (_ for _ in ()).throw(ConnectionError("down")))
    out, status = answer_i18n.localize_answer("주식이란?", "주식은 회사의 소유권을 나눈 증서입니다.", [], "ja-JP")
    assert status == answer_i18n.STATUS_FALLBACK_SUMMARY and out.startswith(text("translation_fallback_empty", "ja-JP"))
    assert not re.search(r"[가-힣]", out)


def test_simple_quote_template_in_each_locale_keeps_numbers():
    r = stock_result(via="simple_quote")
    from app.answer_guard import quote_answer

    ko = f"{quote_answer(r)}\n\n{quote_freshness.line(date(2026, 11, 3), SAT)}"
    rec = [{"tool_name": "stock_tool", "result": r}]
    claims = {"ko-KR": J.extract_claims(ko)}
    for loc in ("en-US", "ja-JP", "zh-CN"):
        out = quote_i18n.localize(ko, rec, loc, now=SAT)
        assert out and "2026-11-03" in out and "222,000" in out and "13,338,444" in out
        assert J.check_language(out, loc, ["가나다전자"]) == [] and J.check_freshness(out, date(2026, 11, 3), 3) == []
        claims[loc] = J.extract_claims(out)
    assert quote_i18n.localize(ko + " 추가 문장", rec, "en-US", now=SAT) is None  # 글자까지 같을 때만


# ---------- 11~14. 숫자 표기·반복 ----------

@pytest.mark.parametrize("raw,want", [
    ("1414000.0원", "1,414,000원"), ("15000.00원", "15,000원"), ("1250.5원", "1,250.5원"), ("-4.260000%", "-4.26%"),
    ("+3.5%", "+3.5%"), ("거래량 13338444주", "거래량 13,338,444주"), ("222,000원", "222,000원"), ("3.14159%", "3.14159%"),
    ("2026년 10월", "2026년 10월"), ("005930", "005930"), ("21만 9천원", "21만 9천원"), ("5주일", "5주일"),
])
def test_number_normalization_keeps_values(raw, want):
    assert answer_tidy.normalize_numbers(raw) == want


def test_normalization_does_not_change_number_judgement():
    raw = "SK하이닉스의 최근 주가는 1414000.0원, 거래량 2291781주, 등락률 -4.460000%"
    vals = [v.value for v in J.find_values(raw)]
    assert [v.value for v in J.find_values(answer_tidy.normalize_numbers(raw))] == vals
    assert J.check_language(answer_tidy.normalize_numbers(raw), "ko-KR") == []  # number_format 없음


def test_repeated_paragraphs_and_sentences_are_removed_once():
    para = "SK하이닉스는 최근 하이브리드 DRAM 개발에 성공했다고 발표했습니다."
    t = f"## 공시\n\n{para}\n\n## 뉴스\n\n{para}\n\n## 위험요인\n\n- 다른 내용의 문장으로 남아야 합니다 이것은."
    out, n = answer_tidy.tidy(t)
    assert n == 1 and out.count(para) == 1 and "## 뉴스" not in out and "## 위험요인" in out
    assert J.check_language(out, "ko-KR") == []


def test_similar_but_different_sentences_are_kept():
    t = "삼성전자 종가는 222,000원입니다. 삼성전자 종가는 230,000원이었습니다.\n\n짧은 문장.\n\n짧은 문장."
    out, n = answer_tidy.tidy(t)
    assert n == 0 and "230,000원" in out and out.count("짧은 문장.") == 2


def test_subheadings_are_not_removed_as_empty():
    t = "## 뉴스·공시 근거\n### 최근 공시\n같은 문장이 반복되어 지워지는지 확인하는 문장입니다.\n### 최근 뉴스\n같은 문장이 반복되어 지워지는지 확인하는 문장입니다."
    out, n = answer_tidy.tidy(t)
    assert n == 1 and "## 뉴스·공시 근거" in out and "### 최근 공시" in out


# ---------- 15·16. 거래량 상위·급증 ----------

@pytest.mark.parametrize("q,kind,market,limit", [
    ("오늘 거래량이 많은 종목 알려줘.", "volume_top", None, 10),
    ("거래량 상위 10개 종목 보여줘.", "volume_top", None, 10),
    ("코스피 거래량 상위 5개 종목 알려줘.", "volume_top", "KOSPI", 5),
    ("최근 거래량 급증 종목 알려줘.", "volume_surge", None, 10),
    ("코스닥 거래량 늘어난 종목", "volume_surge", "KOSDAQ", 10),
    ("삼성전자 거래량 얼마야?", None, None, 10),
    ("거래량이 뭐야?", None, None, 10),
])
def test_volume_question_kinds(q, kind, market, limit):
    assert market_ranking.kind_of(q) == kind and market_ranking.market_of(q) == market and market_ranking.limit_of(q) == limit


def test_volume_top_vs_surge_ranking():
    from app.discovery import rank_volume_surge, rank_volume_top

    items = [{"ticker": "A", "volume": 9_000_000, "volume_ratio": 0.9}, {"ticker": "B", "volume": 100_000, "volume_ratio": 5.0},
             {"ticker": "C", "volume": 3_000_000, "volume_ratio": 2.5}, {"ticker": "D", "volume": None, "volume_ratio": None}]
    assert [i["ticker"] for i in rank_volume_top(items, 3)] == ["A", "C", "B"]
    assert [i["ticker"] for i in rank_volume_surge(items, 3)] == ["B", "C"]


def test_ranking_answer_states_basis_and_difference():
    res = {"found": True, "category": "volume_top", "price_date": "2026-11-03", "window_days": 20,
           "items": [{"company_name": "가나다전자", "ticker": "123450", "volume": 13338444, "change_pct": -3.48}]}
    a = market_ranking.answer(res, "volume_top", "KOSPI", SAT)
    assert "거래량 상위 1개 종목 · 코스피 · 2026-11-03 종가 기준" in a and "13,338,444주" in a and "거래량 급증" in a
    assert "3영업일 지연" in a and J.check_safety(a) == []
    assert "데이터가 없습니다" in market_ranking.answer({"found": False, "message": "x"}, "volume_top", None, SAT)


def test_volume_top_is_not_exposed_to_llm_tool_enum():
    from app.discovery import CATEGORIES, CODE_CATEGORIES

    assert "volume_top" not in CATEGORIES and "volume_top" in CODE_CATEGORIES


# ---------- 17·18. 기간 최고·최저(기간 안 거래일, 비교 기준일 제외) ----------

def test_period_high_low_definition_is_the_same_in_both_paths():
    from datetime import timedelta

    from app.tools.stock_tool import summarize_period

    days = [date(2026, 10, 1) + timedelta(days=i) for i in range(30)]
    days = [d for d in days if d.weekday() < 5][:22]
    closes = [300.0] + [200.0 + i for i in range(21)]  # 기준일(첫 행)이 가장 높다
    rows = [(d, c, 1000) for d, c in zip(days, closes)]
    flow = pf.compute(rows, {"returns": (20,), "mas": (5,), "volume_avg": 20, "window": 20})
    w = flow["window"]
    assert w["from_date"] == days[1].isoformat()  # 비교 기준(20거래일 전) = 두 번째 행
    prices = [{"price_date": d.isoformat(), "close_price": c, "volume": 1000, "change_pct": None} for d, c, _ in rows[2:]]
    base = {"price_date": days[1].isoformat(), "close_price": closes[1]}
    s = summarize_period("가나다전자", prices, base)
    assert w["high"] == max(closes[2:]) and w["low"] == min(closes[2:]) == 201.0  # 첫날(기간 시작일) 포함, 기준일 제외
    assert f"최저는 201원({days[2].isoformat()})" in s["summary"] and f"최고는 220원({days[-1].isoformat()})" in s["summary"]


def test_period_high_on_the_first_day_of_the_period_is_included():
    """기간 첫날(비교 기준일 다음 거래일)이 최고가면 최고가로 나온다(시작일 포함). 비교 기준일은 넣지 않는다."""
    from datetime import timedelta

    from app.tools.stock_tool import summarize_period

    days = [d for d in (date(2026, 10, 1) + timedelta(days=i) for i in range(40)) if d.weekday() < 5][:21]
    closes = [100.0, 500.0] + [300.0 - i for i in range(19)]  # 기준일 100, 기간 첫날 500이 최고, 마지막 날 282가 최저
    rows = [(d, c, 1000) for d, c in zip(days, closes)]
    w = pf.compute(rows, {"returns": (20,), "mas": (5,), "volume_avg": 20, "window": 20})["window"]
    assert w["from_date"] == days[0].isoformat() and w["high"] == 500.0 and w["high_date"] == days[1].isoformat()
    assert w["low"] == 282.0 and w["low_date"] == days[-1].isoformat()  # 기준일 100은 최저가 아니다
    prices = [{"price_date": d.isoformat(), "close_price": c, "volume": 1000, "change_pct": None} for d, c, _ in rows[1:]]
    s = summarize_period("가나다전자", prices, {"price_date": days[0].isoformat(), "close_price": 100.0})
    assert f"최고는 500원({days[1].isoformat()})" in s["summary"] and f"최저는 282원({days[-1].isoformat()})" in s["summary"]


def test_price_gap_days_are_not_filled():
    """시세가 빠진 날은 채우지 않는다: 기간은 저장된 거래일 행만으로 세고, 빠진 날의 값을 만들지 않는다."""
    from datetime import timedelta

    days = [d for d in (date(2026, 10, 1) + timedelta(days=i) for i in range(40)) if d.weekday() < 5][:23]
    missing = days[10]
    rows = [(d, 1000.0 + i, 1000) for i, d in enumerate(days) if d != missing]
    w = pf.compute(rows, {"returns": (20,), "mas": (5,), "volume_avg": 20, "window": 20})["window"]
    assert w["days"] == 20 and missing.isoformat() not in (w["high_date"], w["low_date"], w["from_date"])


# ---------- 휴장일·지연 이유(2026-11-08): 휴장일 달력 대신 전망 답과 같은 수집 기록 추정 ----------

HOLIDAY = {"expected_date": "2026-10-09", "status": outlook.PRICE_STATUS_HOLIDAY,
           "detail": "2026-10-09 19:30 수집에서 시장 전체에 그날 시세 없음"}
MON_AM = datetime(2026, 10, 12, 10, 0, tzinfo=KST)  # 한글날(10-09 금) 다음 월요일 장중: 기대 거래일 10-09(평일 기준)


def test_holiday_reason_is_stated_and_lag_count_is_unchanged():
    t = quote_freshness.line(date(2026, 10, 8), MON_AM, price_status=HOLIDAY)
    assert "1영업일 이전 데이터" in t and "늦은 이유(수집 기록으로 추정): 휴장일 추정(2026-10-09 19:30" in t
    assert quote_freshness.status(date(2026, 10, 8), MON_AM)["lag"] == 1  # 수는 전망·GET /api/status와 같다
    en = quote_freshness.line(date(2026, 10, 8), MON_AM, "en-US", HOLIDAY)
    assert "presumed market holiday" in en and not re.search(r"[가-힣]", en)
    assert "休場日と推定" in quote_freshness.line(date(2026, 10, 8), MON_AM, "ja-JP", HOLIDAY)
    assert "推定为休市日" in quote_freshness.line(date(2026, 10, 8), MON_AM, "zh-CN", HOLIDAY)
    # 최신이면 이유를 붙이지 않는다
    assert "늦은 이유" not in quote_freshness.line(date(2026, 10, 8), datetime(2026, 10, 8, 18, 0, tzinfo=KST), price_status=HOLIDAY)
    assert quote_freshness.reason_text({"status": outlook.PRICE_STATUS_LATEST}) is None


def test_simple_quote_carries_reason_into_locale_template(monkeypatch):
    monkeypatch.setattr(agent, "_run_tool", lambda name, args: stock_result(d="2026-10-08"))
    monkeypatch.setattr(agent, "_quote_price_status", lambda base, now: HOLIDAY)
    monkeypatch.setattr(agent, "datetime", type("D", (datetime,), {"now": staticmethod(lambda tz=None: MON_AM)}))
    resp, records = agent._answer_simple_quote("가나다전자 종가 알려줘", [_Co()])
    a = resp["answer"]
    assert "휴장일 추정" in a and records[0]["result"]["price_status"] == HOLIDAY
    out = quote_i18n.localize(a, records, "en-US", now=MON_AM)
    assert out and "presumed market holiday" in out and "2026-10-08" in out


def test_price_status_lookup_is_skipped_when_fresh_and_failure_is_safe(monkeypatch):
    monkeypatch.undo()  # autouse 고정을 풀고 실제 함수를 본다
    calls = []
    monkeypatch.setattr(agent, "SessionLocal", lambda: calls.append("opened"))
    assert agent._quote_price_status(date(2026, 11, 6), FRI_EVE) is None and calls == []  # 지연이 없으면 DB를 열지 않는다

    class _Sess:
        def close(self):
            calls.append("closed")

    monkeypatch.setattr(agent, "SessionLocal", _Sess)
    monkeypatch.setattr(outlook, "price_status", lambda db, base, now: (_ for _ in ()).throw(RuntimeError("query failed")))
    assert agent._quote_price_status(date(2026, 11, 3), SAT) is None and calls == ["closed"]  # 조회 실패 -> 이유 없이 답


def test_order_guide_never_places_an_order(monkeypatch):
    """주문 안내 경로는 시세만 읽는다. 체결 함수(app.portfolio.place_order)를 부르지 않고 기록도 stock_tool 하나뿐이다."""
    from app import portfolio

    monkeypatch.setattr(portfolio, "place_order", lambda *a, **k: pytest.fail("채팅 주문 안내가 체결을 호출함"))
    monkeypatch.setattr(agent, "_question_companies", lambda q: [_Co()])
    monkeypatch.setattr(agent, "_run_tool", lambda name, args: stock_result())
    for q in ("가나다전자 10주 사줘", "가나다전자 5주 매도해줘"):
        resp, records = agent._answer_order_command(q)
        assert resp["answer"].startswith(outlook.ORDER_GUIDE_ANSWER) and [r["tool_name"] for r in records] == ["stock_tool"]


# ---------- 휴장일 질문의 직전 시세가 수집 지연일 때(2026-11-08, live lv_27) ----------

def test_closed_day_with_stale_latest_price_does_not_say_previous_trading_day():
    from app import period_answers as pa

    prev = {"price_date": "2026-11-03", "close_price": 222000.0}
    note = quote_freshness.line(date(2026, 11, 3), datetime(2026, 11, 8, 12, 0, tzinfo=KST))
    t = pa.closed_day_text("가나다전자", date(2026, 11, 7), pa.REASON_WEEKEND, prev, note)
    assert "직전 거래일" not in t and "저장된 마지막 시세인 2026-11-03 가나다전자 종가는 222,000원" in t
    assert "3영업일 지연" in t and J.check_freshness(t, date(2026, 11, 3), 3) == []
    # 지연이 아니면 예전 문장 그대로
    assert pa.closed_day_text("가나다전자", date(2026, 11, 7), pa.REASON_WEEKEND, {"price_date": "2026-11-06", "close_price": 1.0}) \
        == "2026-11-07(토)은 주말이라 휴장이고 시세 데이터가 없습니다. 직전 거래일(2026-11-06) 가나다전자 종가는 1원입니다."


class _Row:
    def __init__(self, d, close):
        self.price_date, self.close_price, self.change_pct, self.volume = d, close, -3.48, 100
        self.open_price = self.high_price = self.low_price = None


@pytest.mark.parametrize("day,prev,data_end,stale", [
    (date(2026, 11, 7), date(2026, 11, 3), date(2026, 11, 3), True),    # 토요일, 마지막 시세 11-03 < 직전 평일 11-06: 수집 지연
    (date(2026, 11, 7), date(2026, 11, 6), date(2026, 11, 6), False),   # 직전 평일 시세가 있음
    (date(2025, 10, 4), date(2025, 10, 2), date(2026, 11, 3), False),   # 과거 휴장(개천절 다음 토요일): 마지막 데이터가 아니면 예전대로
])
def test_history_closed_day_flags_only_latest_stale_data(monkeypatch, day, prev, data_end, stale):
    monkeypatch.setattr(agent, "price_rows_between", lambda db, cid, a, b: [])
    monkeypatch.setattr(agent, "last_row_before", lambda db, cid, d: _Row(prev, 222000.0))
    monkeypatch.setattr(agent, "market_has_rows", lambda db, d: False)
    from app.period_scope import RequestedPeriod

    text_, result = agent._history_day_parts(None, _Co(), day, RequestedPeriod(day, day, "어제"), data_end, "어제 종가")
    assert ("직전 거래일" not in text_ and "데이터 기준일" in text_) is stale, text_


def test_consecutive_identical_short_lines_are_collapsed():
    """(2026-11-08 live lv_05·lv_15) 짧은 같은 줄의 연속 반복은 한 줄만 남긴다. 떨어진 짧은 같은 문장, 다른 줄은 남긴다."""
    t = ("## 공시\n\n*   카카오 계열사 CEO 총출동\n" + "*   기업설명회(IR)개최(안내공시)\n" * 8 + "*   결산실적공시예고(안내공시)\n\n"
         "분사에는 관심이 없다고 선을 그었다.\n분사에는 관심이 없다고 선을 그었다.")
    out, n = answer_tidy.tidy(t)
    assert out.count("기업설명회(IR)개최(안내공시)") == 1 and "결산실적공시예고" in out and "CEO 총출동" in out
    assert out.count("분사에는 관심이 없다고") == 1 and n == 8
    assert J.check_language(out, "ko-KR") == []
    # 줄 사이에 다른 줄이 있으면 짧은 줄 반복은 남긴다(표·목록의 같은 값)
    keep = "- 보합\n- 상승 종목\n- 보합"
    assert answer_tidy.tidy(keep) == (keep, 0)
