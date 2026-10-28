"""(B) 답변 숫자 검증(app/answer_guard.py)과 (C) 데이터 기준일 표시. DB·Ollama 없이 돈다."""

from datetime import date

import pytest

from app import answer_guard as G
from evals import metrics as M

STOCK = {"tool_name": "stock_tool", "arguments": {"ticker": "NAVER", "period_days": 1}, "result": {
    "found": True, "company_name": "NAVER", "change_pct": 1.3, "volume": 592942,
    "prices": [{"price_date": "2026-10-14", "close_price": 179200.0, "change_pct": 1.3, "volume": 592942}]}}
MULTI = {"tool_name": "stock_tool", "arguments": {}, "result": {
    "found": True, "company_name": "기아", "summary": "기아의 2026-10-14 종가는 108,200원으로 ...",
    "prices": [{"price_date": "2026-10-13", "close_price": 107400.0, "change_pct": -0.5, "volume": 1200000},
               {"price_date": "2026-10-14", "close_price": 108200.0, "change_pct": 0.74, "volume": 1500000}]}}


# ---------- 평가기와 같은 추출 규칙(동기화) ----------

SYNC_CORPUS = [
    "1. 삼성전자의 2026-10-14 종가는 70,000원으로 20거래일간 -2.59% 하락했습니다.",
    "2. 거래량은 1,234만 주이며 10월 14일 기준입니다. KODEX 200, S&P500, 005930.",
    "시가총액 약 418조원, 3건의 뉴스. 100달러, 10월에 15시에 발표",
    "거래량은 23만 7866주, 1조 2,345억원, 7만 2,000원, 3만 5만원",
    "등락률 +1.30%, 0.5%p, 2배, 3.5퍼센트, 거래량 3,494,666 건",
    "NAVER의 현재 주가는 1,792,000원입니다. 08:36 기준, 2026.10.14, 2026년 3월, 3개월, 5일",
]


@pytest.mark.parametrize("text", SYNC_CORPUS)
def test_extraction_matches_the_evaluator(text):
    """서비스(app/answer_guard)와 평가기(evals/metrics)의 숫자 추출 결과가 같아야 한다. 한쪽만 바꾸면 여기서 깨진다."""
    ours = [(n.value, n.raw, n.tolerance, n.unit) for n in G.extract_numbers(text)]
    theirs = [(n.value, n.raw, n.tolerance, n.unit) for n in M.extract_numbers(text)]
    assert ours == theirs


def test_exclusion_patterns_are_identical():
    assert G._EXCLUDE_RE.pattern == M._EXCLUDE_RE.pattern
    assert G._NUM_RE.pattern == M._NUM_RE.pattern and G._COMPOUND_RE.pattern == M._COMPOUND_RE.pattern


# ---------- 정상 답변은 바꾸지 않는다(오탐 없음) ----------

@pytest.mark.parametrize("answer", [
    "NAVER의 2026-10-14 종가는 179,200원입니다.",
    "NAVER 종가 179,200원, 등락률 +1.30%, 거래량 592,942주입니다.",
    "NAVER 종가는 약 17.9만원이고 1.3% 올랐습니다.",
    "거래량은 59만 2942주입니다.",
    "거래량은 약 59.3만 주입니다.",
    "종가는 179,200 원, 등락률은 1.3 % 입니다.",
    "1. 2026년 10월 14일 기준 최근 1거래일 데이터입니다.",
    "NAVER 주가 알려달라고 하셨죠. 종가는 179,200원입니다.",
])
def test_supported_answers_pass_unchanged(answer):
    out, log = G.guard_answer(answer, [STOCK], "NAVER 주가 얼마야?")
    assert out == answer and log["replaced"] is False and log["violations"] == []


def test_cheon_compound_is_read_as_one_value():
    """'17만 9천원'은 추출 규칙(평가기와 동기화)상 '17만'과 '9천원'으로 나뉜다. 2026-10-28부터 검증 단계에서 합친 값
    (179,000)으로 다시 확인해 정상 답변을 코드 요약으로 바꾸지 않는다. 합친 값도 틀리면 그대로 위반이다."""
    out, log = G.guard_answer("NAVER 종가는 약 17만 9천원입니다.", [STOCK], "NAVER 주가")
    assert log["violations"] == [] and out == "NAVER 종가는 약 17만 9천원입니다."
    assert G.guard_answer("NAVER 종가는 약 17만 5천원입니다.", [STOCK], "NAVER 주가")[1]["violations"] == ["5천원"]


def test_derived_difference_and_rate_are_supported():
    # 두 날의 종가 차이(800원), 변화율(0.74%), 거래량 차이(300,000주)는 도구 결과의 단순 계산이다
    answer = "기아는 전일보다 800원(0.74%) 올랐고 거래량은 30만 주 늘었습니다."
    out, log = G.guard_answer(answer, [MULTI], "기아 주가")
    assert log["violations"] == [] and out == answer


def test_question_numbers_are_supported():
    out, log = G.guard_answer("100주를 사면 17,920,000원입니다.", [STOCK], "NAVER 100주 사면 얼마야?")
    assert log["violations"] == ["17,920,000원"]  # 곱셈 결과는 단순 계산(차이·변화율)에 넣지 않았다
    assert G.guard_answer("100주 기준입니다.", [STOCK], "NAVER 100주 사면 얼마야?")[1]["violations"] == []


# ---------- 위반 답변은 확실히 바꾼다 ----------

def test_wrong_digits_are_replaced_with_code_summary():
    out, log = G.guard_answer("NAVER의 현재 주가는 1,792,000원입니다.", [STOCK], "NAVER 주가 얼마야?")
    assert log == {"replaced": True, "violations": ["1,792,000원"], "mode": "code_summary",
                   "kinds": {"number": 1, "date": 0, "target": 0, "period": 0},
                   "original": "NAVER의 현재 주가는 1,792,000원입니다."}  # 대체 전 원문(내부 기록, 오탐 분석용)
    assert "179,200원" in out and "1,792,000" not in out
    assert "+1.30%" in out and "592,942주" in out


def test_made_up_change_is_replaced():
    answer = "기아의 주가는 108,200 원으로 0.74% 상승했습니다. 거래량은 1,500,000 주로 전일보다 794,309 주 증가했습니다."
    out, log = G.guard_answer(answer, [MULTI], "기아 주가와 거래량 알려줘")
    assert log["replaced"] is True and "794,309 주" in log["violations"]
    assert "794,309" not in out and out.startswith("## 주가 동향\n기아의 2026-10-14 종가는 108,200원")


def test_without_tool_results_only_bad_value_sentences_are_dropped():
    answer = "코스피는 장기적으로 성장해 왔습니다. 오늘 코스피는 2,750.5포인트입니다.\n분산 투자가 일반적입니다."
    out, log = G.guard_answer(answer, [], "코스피 어때?")
    assert log["mode"] == "drop_sentences"
    assert "2,750.5" not in out and "장기적으로 성장" in out and "분산 투자" in out


def test_code_summary_parts():
    news = {"tool_name": "news_tool", "result": {"found": True, "news": [
        {"title": "NAVER, 3분기 실적 발표", "published_at": "2026-10-13T08:00:00"}]}}
    fx = {"tool_name": "fx_tool", "result": {"found": True, "latest_date": "2026-10-14", "latest_rate": 1385.5,
                                              "daily_change_pct": -0.21}}
    text = G.code_summary([STOCK, news, fx])
    assert "## 주가 동향\nNAVER의 2026-10-14 종가는 179,200원입니다." in text
    assert "2026-10-14 원/달러 환율은 1,385.50원입니다. 전일 대비 -0.21%입니다." in text
    assert "## 뉴스·공시 근거\n- NAVER, 3분기 실적 발표 (2026-10-13)" in text
    assert G.code_summary([{"tool_name": "stock_tool", "result": {"found": False}}]) is None
    # 코드 요약의 숫자는 모두 도구 결과로 설명된다
    assert G.unsupported_numbers(text, [STOCK["result"], news["result"], fx["result"]]) == []


# ---------- 단순 시세 질문 ----------

def _simple(q):
    from app.period_parser import mentions_period, parse_period

    p = parse_period(q)
    return G.is_simple_quote(q, mentions_period(q), p.matched_text if p else None)


@pytest.mark.parametrize("q", [
    "NAVER 주가 얼마야?", "기아 주가와 거래량 알려줘", "LG에너지솔루션 오늘 등락률은?", "삼성전자 최근 종가와 등락률 알려줘",
    "SK하이닉스 거래량 얼마야?", "KODEX 200 최근 가격 알려줘", "현대차 현재가 알려줘", "SK하이닉스 지금 얼마야?",
])
def test_simple_quote_questions(q):
    assert _simple(q)


@pytest.mark.parametrize("q", [
    "카카오 주가 왜 떨어졌어?", "현대차 지난 한 달 주가 등락률은?", "삼성전자 주가랑 뉴스 알려줘", "HD현대 최근 5거래일 수익률은?",
    "NAVER 요즘 주가 어때?", "셀트리온 주가 빠진 게 장 전체가 약해서야?", "삼성전자 코스피 대비 수익률 알려줘",
    "셀트리온 최근 20거래일 최고가와 최저가는?", "삼성전자 주가 흐름 알려줘", "삼성전자 주가, 뉴스, 공시 같이 확인해줘",
    "삼성전자 PER 얼마야?", "요즘 원달러 환율 얼마야?", "삼성전자 실적 발표 이후 주가 반응은?",
])
def test_not_simple_quote_questions(q):
    assert not _simple(q)


def test_quote_answer_uses_only_tool_values():
    text = G.quote_answer(STOCK["result"])
    assert text == "NAVER의 2026-10-14 종가는 179,200원입니다. 전일 대비 등락률은 +1.30%, 거래량은 592,942주입니다."
    assert G.quote_answer({"found": False}) is None


# ---------- (C) 데이터 기준일 ----------

TODAY = date(2026, 10, 27)


@pytest.mark.parametrize("q, period_text, expected", [
    ("NAVER 지금 얼마야?", None, "마지막 데이터는 2026-10-14 기준입니다."),
    ("NAVER 오늘 주가 얼마야?", "오늘", "마지막 데이터는 2026-10-14 기준입니다."),
    ("NAVER 현재가 알려줘", None, "마지막 데이터는 2026-10-14 기준입니다."),
    ("NAVER 주가 얼마야?", None, None),  # 현재 시점 표현 없음
    ("NAVER 최근 3개월 주가", "3개월", None),  # 기간을 명시한 질문은 대상 아님
    ("NAVER 지금 뉴스 알려줘", None, None),  # 시세 질문이 아님
])
def test_freshness_note(q, period_text, expected):
    assert G.freshness_note(q, [STOCK], TODAY, period_text) == expected


def test_freshness_note_not_added_when_data_is_current():
    assert G.freshness_note("NAVER 지금 얼마야?", [STOCK], date(2026, 10, 15), None) is None  # 1영업일 차이
    assert G.freshness_note("NAVER 지금 얼마야?", [], TODAY, None) is None


def test_freshness_note_satisfies_the_evaluator():
    answer = G.quote_answer(STOCK["result"]) + "\n\n" + G.freshness_note("NAVER 지금 얼마야?", [STOCK], TODAY, None)
    assert M.data_date_ok(answer, date(2026, 10, 14), TODAY) is True


# ---------- 식별자 필드는 숫자 근거가 아니다(2026-10-28) ----------

LGCHEM = {"tool_name": "stock_tool", "arguments": {"ticker": "LG화학", "period_days": 1}, "result": {
    "found": True, "ticker": "051910", "company_name": "LG화학", "change_pct": -2.34, "volume": 402118,
    "prices": [{"price_date": "2026-10-27", "close_price": 312500.0, "volume": 402118, "change_pct": -2.34}]}}


def test_ticker_code_is_not_evidence():
    """종목코드 051910이 51,910으로 읽혀 지어낸 '52,000주'가 통과했다(검증기 holdout hv10)."""
    out, log = G.guard_answer("LG화학 거래량은 전일보다 52,000주 줄었습니다.", [LGCHEM], "LG화학 거래량 어때?")
    assert log["replaced"] is True and log["violations"] == ["52,000주"]


def test_price_equal_to_the_code_still_passes():
    """가격이 우연히 종목코드와 같은 값이어도 가격 필드에 있으면 근거다(오탐 없음)."""
    rec = {"tool_name": "stock_tool", "arguments": {}, "result": {
        "found": True, "ticker": "051910", "company_name": "가상종목",
        "prices": [{"price_date": "2026-10-27", "close_price": 51910.0, "volume": 1000, "change_pct": 1.0}]}}
    assert G.guard_answer("가상종목의 종가는 51,910원입니다.", [rec], "")[1]["replaced"] is False


def test_numbers_inside_urls_and_ids_are_not_evidence():
    rec = {"tool_name": "news_tool", "arguments": {}, "result": {"found": True, "company_name": "삼성전자", "news": [
        {"title": "삼성전자 소식", "url": "https://n.news.naver.com/mnews/article/030/0003479457", "id": 812345}]}}
    assert G.support_values(rec["result"]) == []  # url 속 030·0003479457, id 812345는 근거가 아니다
    _, log = G.guard_answer("관련 기사 번호는 3,479,457원입니다.", [rec], "")
    assert log["violations"] == ["3,479,457원"]


def test_evaluator_support_values_is_unchanged():
    """평가기(evals/metrics.py)의 판정은 바꾸지 않았다: 평가기는 여전히 종목코드도 근거로 센다(서비스가 더 엄격)."""
    assert 51910.0 in M.support_values(LGCHEM["result"]) and 51910.0 not in G.support_values(LGCHEM["result"])


def test_replaced_answer_original_is_logged_up_to_2000_chars():
    long = "NAVER의 현재 주가는 1,792,000원입니다. " + "가" * 3000
    _, log = G.guard_answer(long, [STOCK], "NAVER 주가")
    assert log["replaced"] and len(log["original"]) == G.ORIGINAL_LOG_CHARS == 2000
