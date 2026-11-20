"""68차(2026-11-17) 회귀 테스트: 실사용 #43444~#43447과 KODEX 200 RAG 0건. DB·Ollama 없이 돈다(분석 결과·근거 조회는 가짜).

1 원인+7일 전망(#43444) 2 방향 직접 질문 3 같은 결론 반복 방지(#43446) 4 3개월 후(#43447) 5 KODEX 200 자금 유입
6 뉴스 없는 종목 7 공시 본문 없는 종목 8 시세 3영업일 지연 9 ETF·주식 검색 구분 10 다른 종목으로 바꾸는 후속 질문
"""
from datetime import date, datetime
from types import SimpleNamespace as NS

import pytest

from app import agent, answer_cause, outlook
from app.news_relevance import subject_match
from app.tools import rag_search_tool as rag
from tests.test_outlook_round23 import COMPANY, KST, _rows, backtest  # noqa: F401  (backtest는 픽스처)

SAMSUNG = NS(id=1, name="삼성전자", ticker="005930", market="KOSPI", asset_type="stock")
KODEX = NS(id=303, name="KODEX 200", ticker="069500", market="KOSPI", asset_type="etf")
Q1 = "하이닉스가 왜 올랐어? 그리고 앞으로 7일간 오를 거 같아? 내릴 거 같아?"


def _res(period="1주일", now=None, lag_days=0):
    rows = _rows()
    p = outlook.PERIODS[period]
    ind = outlook.compute_indicators(rows, p)
    sig = outlook.classify_signals(ind, p)
    base = date.fromisoformat(ind["base_date"])
    now = now or datetime(base.year, base.month, base.day, 18, 0, tzinfo=KST)
    data = outlook.data_check(base, now, [])
    return {"indicators": ind, "signals": sig, "label": outlook.judge(sig, data), "data": data}, now


class _DB:
    def close(self):
        pass


@pytest.fixture
def route(monkeypatch, backtest):  # noqa: F811
    """전망 경로를 DB 없이 돌린다. state["companies"]가 질문 속 종목, state["found"]가 원인 단락 근거."""
    state = {"companies": [COMPANY], "found": {"news": [], "disclosures": [], "after_count": 0, "after_first": None},
             "lag": 0, "by_name": {"SK하이닉스": COMPANY, "삼성전자": SAMSUNG, "KODEX 200": KODEX}}

    def analyze(db, company, period, now):
        res, _ = _res(period.label)
        return res

    monkeypatch.setattr(agent, "_question_companies", lambda q: list(state["companies"]))
    monkeypatch.setattr(agent, "_company_by_name", lambda name: state["by_name"].get(name))
    monkeypatch.setattr(agent, "SessionLocal", _DB)
    monkeypatch.setattr(outlook, "analyze", analyze)
    monkeypatch.setattr(answer_cause, "collect", lambda db, company, base, start: dict(state["found"]))
    monkeypatch.setattr(agent, "_llm_chat", lambda **k: pytest.fail("전망 경로는 채팅 LLM을 부르지 않는다"))
    agent._reset_llm_state()
    return state


def _ask(question, previous=None):
    resp, records = agent._answer_forecast(question, previous)
    return resp["answer"], records


# ---------- 1. 원인 + 7일 전망(#43444) ----------

def test_cause_and_7day_forecast_answers_both_with_direction_first(route):
    route["found"] = {"news": [{"company_name": "SK하이닉스", "title": "SK하이닉스, HBM4 양산", "date": "2026-11-10", "source": "a.kr",
                               "url": "https://a.kr/1", "published_at": "2026-11-10T09:00:00+09:00"}],
                      "disclosures": [], "after_count": 7, "after_first": "2026-11-16"}
    text, records = _ask(Q1)
    lines = [x for x in text.splitlines() if x.strip()]
    assert lines[0].startswith("기간 기준: 질문의 7일은 달력 기준이라 약 5거래일")
    assert lines[1].startswith("SK하이닉스(000660) · 향후 5거래일 · 방향: ")  # 결론(방향)이 원인보다 먼저
    assert text.index("· 방향:") < text.index("## 최근 주가 움직임과 원인")
    assert "SK하이닉스, HBM4 양산" in text and "직접 원인이라고 단정할 수는 없습니다" in text
    assert "기준일 뒤(2026-11-16~)에 수집된 이 종목 기사 7건" in text and "원인 후보에서 뺐습니다" in text
    assert [r["tool_name"] for r in records][-2:] == ["news_tool", "disclosure_tool"]
    assert outlook.marker_of(text)["period"] == "1주일"  # 다음 후속 질문이 종목·기간을 잇는다


def test_cause_forecast_is_detected_but_why_followup_is_not():
    assert outlook.is_cause_forecast_question(Q1)
    assert outlook.is_cause_forecast_question("카카오 왜 떨어졌어? 앞으로 오를까?")
    assert not outlook.is_cause_forecast_question("삼성전자 오늘 왜 올랐어?")  # 원인만 묻는 질문은 기존 흐름
    assert not outlook.is_cause_forecast_question("삼성전자 앞으로 오를까?")


def test_premise_is_checked_against_actual_move():
    mv = {"r": -0.79, "day": 1.2}
    assert "질문은 '올랐다'고 하셨지만" in answer_cause.premise_line("왜 올랐어?", mv)
    assert "기준일 하루는 +1.20% 올랐습니다" in answer_cause.premise_line("왜 올랐어?", mv)
    assert answer_cause.premise_line("왜 올랐어?", {"r": 2.0, "day": 1.0}) is None
    assert "질문은 '내렸다'고 하셨지만" in answer_cause.premise_line("왜 떨어졌어?", {"r": 3.1, "day": -0.5})
    assert answer_cause.premise("왜 올랐다가 떨어졌어?") is None


# ---------- 2. 방향 직접 질문 ----------

def test_direct_direction_question_leads_with_conclusion(route):
    text, _ = _ask("SK하이닉스 내릴 거 같아 오를 거 같아?")
    first = text.splitlines()[0]
    assert first.startswith("SK하이닉스(000660) · 향후 5거래일 · 방향: ")
    second = [x for x in text.splitlines() if x.strip()][1]
    assert second.startswith(("상승 쪽입니다", "하락 쪽입니다", "현재는 상승과 하락 중", "데이터가 부족해"))
    assert "확률" not in first and "보장" not in text


# ---------- 3. 같은 결론 반복 방지(#43446) ----------

def test_same_conclusion_is_not_reproduced(route):
    first, _ = _ask("SK하이닉스 일주일 뒤 오를까?")
    prev = {"report_id": 1, "question": "SK하이닉스 일주일 뒤 오를까?", "answer": first, "company_name": "SK하이닉스"}
    route["companies"] = []
    again, records = _ask("그럼 앞으로 어떨 거 같은지 예상해줘", prev)
    assert "앞선 답과 같은 결론" in again.splitlines()[0]
    assert again != first and len(again) < len(first) + 200
    assert "과거 검증(검증 구간" not in again  # 긴 과거 검증 문단을 다시 찍지 않는다
    assert outlook.marker_of(again)["period"] == "1주일" and records


def test_new_base_date_is_not_treated_as_repeat(route):
    first, _ = _ask("SK하이닉스 일주일 뒤 오를까?")
    res, _ = _res()
    older = first.replace(res["indicators"]["base_date"], "2026-01-02")
    prev = {"report_id": 1, "question": "q", "answer": older, "company_name": "SK하이닉스"}
    route["companies"] = []
    again, _ = _ask("그럼 앞으로 어떨 거 같은지 예상해줘", prev)
    assert "앞선 답과 같은 결론" not in again


# ---------- 4. 3개월 후(#43447) ----------

def test_three_months_later_after_forecast_is_a_60_day_forecast(route):
    first, _ = _ask("SK하이닉스 일주일 뒤 오를까?")
    prev = {"report_id": 1, "question": "q", "answer": first, "company_name": "SK하이닉스"}
    route["companies"] = []
    text, records = _ask("3개월 후는?", prev)
    assert text.splitlines()[0].startswith("SK하이닉스(000660) · 향후 60거래일 · 방향: ")
    assert outlook.marker_of(text)["period"] == "3개월"
    assert "67거래일" not in text and "원인" not in text
    assert records[0]["arguments"]["period"] == "3개월"


@pytest.mark.parametrize("q", ["3개월 후는?", "그럼 한 달 뒤엔 어때?", "1개월은?", "그러면 다음 주는?", "3개월 뒤엔 어때?"])
def test_period_only_followups(q):
    assert outlook.is_period_followup(q)


@pytest.mark.parametrize("q", ["3개월 동안 주가 흐름 보여줘", "최근 1개월 뉴스는?", "SK하이닉스 3개월 수익률", "3개월 전에는 얼마였어?"])
def test_not_period_only_followups(q):
    assert not outlook.is_period_followup(q)


def test_future_horizon_does_not_override_llm_period_as_past_window():
    calls = [{"function": {"name": "stock_tool", "arguments": {"ticker": "SK하이닉스", "period_days": 5}}}]
    assert agent._apply_period_override(calls, "SK하이닉스 3개월 후 주가는?") == {}
    assert calls[0]["function"]["arguments"]["period_days"] == 5
    past = [{"function": {"name": "stock_tool", "arguments": {"ticker": "SK하이닉스", "period_days": 5}}}]
    assert agent._apply_period_override(past, "SK하이닉스 최근 3개월 주가") != {}  # 과거 기간은 기존처럼 덮어쓴다


# ---------- 5·9. KODEX 200 자금 유입, ETF·주식 검색 구분 ----------

def test_kodex200_flow_articles_match_by_lead_and_unrelated_ones_do_not():
    lead = "자금 유입 규모가 가장 큰 ETF는 KODEX 200으로 전 거래일 기준 1524억원이 순유입됐다."
    assert subject_match("KODEX 200", "etf", "[ETF 시황] K팝 웃고 중국 테마 울고…수익률 희비", lead) == "content_lead"
    assert subject_match("KODEX 200", "etf", "KODEX 200에 매수세 유입…82,060원 형성", "") == "title"
    late = "CD금리투자KIS(합성)'에도 각각 1444억원, 1201억원이 유입됐다. 개인 투자자 순매수 기준으로는 'KODEX 200'"
    assert subject_match("KODEX 200", "etf", "서학개미, 일주일 새 3조원 순매수", late) is None  # 첫머리 밖 언급은 근거가 아니다
    other = ["KODEX 200타겟위클리커버드콜"]
    assert subject_match("KODEX 200", "etf", "KODEX 200타겟위클리커버드콜 분배금", "", other_etf_names=other) is None


def test_stock_lead_match_ignores_longer_company_names():
    assert subject_match("카카오", "stock", "3Q 실적", "카카오모빌리티가 올해 3분기 역대 최대 매출을 기록했지만") is None
    assert subject_match("카카오", "stock", "핀테크株 온도차", "카카오는 플랫폼 경쟁력이 부각되며") == "content_lead"


def test_select_news_keeps_order_drops_unrelated_and_duplicates_and_counts():
    items = [({"title": "엔비디아 급락에 1061억", "score": 0.66}, "순유입 1위는 1조515억원이 들어온 'KODEX 200'이었다."),
             ({"title": "엔비디아 쇼크 SOXL", "score": 0.64}, "반도체 3배 레버리지 SOXL과 나스닥 3배 TQQQ에 개인 순매수가 몰렸고 예탁금도 늘었다. 국내 상품 가운데서는 KODEX 200도 있었다"),
             ({"title": "KODEX 200에 매수세 유입", "score": 0.63}, ""),
             ({"title": "KODEX 200에 매수세 유입!", "score": 0.62}, "")]
    kept, excluded = rag.select_news("KODEX 200", "etf", items)
    assert [k["title"] for k in kept] == ["엔비디아 급락에 1061억", "KODEX 200에 매수세 유입"]
    assert [k["match"] for k in kept] == ["content_lead", "title"]
    assert excluded == {"subject_not_in_title_or_lead": 1, "duplicate_title": 1}


def test_empty_result_distinguishes_no_embeddings_from_filtered():
    search = {"candidates": {"news": 30, "disclosure": 0}, "kept_news": 0, "excluded": {"subject_not_in_title_or_lead": 30}}
    filtered = rag.empty_response("q", "KODEX 200", 5, 203, search, {})
    assert filtered["reason"] == "no_relevant_match" and "임베딩 데이터가 없는 것이 아닙니다" in filtered["message"]
    assert "검색 후보 30건" in filtered["message"] and filtered["search"] == search
    none = rag.empty_response("q", "KODEX 200", 5, 0, {"candidates": {"news": 0, "disclosure": 0}}, {})
    assert none["reason"] == "no_embeddings" and "임베딩된 news/disclosure 데이터가 없어" in none["message"]


def test_etf_cause_section_has_no_disclosure_record(route):
    route["companies"] = [KODEX]
    text, records = _ask("KODEX 200 왜 떨어졌어? 앞으로 오를까?")
    assert "disclosure_tool" not in [r["tool_name"] for r in records]
    assert "직접 다룬 기사를 찾지 못해" in text  # ETF는 공시를 수집하지 않으므로 "기사"만


# ---------- 6. 뉴스 없는 종목 ----------

def test_no_news_in_window_says_cause_cannot_be_identified(route):
    text, records = _ask(Q1)
    assert "직접 다룬 기사·공시를 찾지 못해 움직임의 원인을 특정할 수 없습니다" in text
    news = next(r for r in records if r["tool_name"] == "news_tool")
    assert news["result"]["found"] is False and news["result"]["news"] == []


# ---------- 7. 공시 본문 없는 종목 ----------

def test_cause_section_lists_disclosure_by_title_only_and_groups_same_titles(route):
    route["found"] = {"news": [], "disclosures": [{"company_name": "SK하이닉스", "title": "[기재정정]주식매수선택권부여에관한신고",
                                                 "date": "2026-11-05", "count": 3, "source_url": "https://dart.fss.or.kr/x",
                                                 "disclosed_at": "2026-11-05T00:00:00+09:00"}],
                      "after_count": 0, "after_first": None}
    text, _ = _ask(Q1)
    assert text.count("주식매수선택권부여에관한신고") == 1 and "(같은 제목 3건)" in text


def test_rag_disclosure_flags_missing_content():
    src = open(rag.__file__, encoding="utf-8").read()
    assert '"content_available": bool((row.content or "").strip())' in src


# ---------- 8. 시세 3영업일 지연 ----------

def test_stale_prices_keep_warning_in_cause_and_repeat_answers(route, monkeypatch):
    def stale(db, company, period, now):
        res, base_now = _res(period.label)
        base = date.fromisoformat(res["indicators"]["base_date"])
        res["data"] = outlook.data_check(base, datetime(base.year, base.month, base.day + 3, 18, 0, tzinfo=KST)
                                         if base.day <= 25 else base_now, [])
        res["data"]["warnings"] = res["data"]["warnings"] or ["마지막 시세가 기대 거래일보다 3영업일 지연된 데이터입니다."]
        res["data"]["freshness"] = "3영업일 지연(미수집)"
        return res

    monkeypatch.setattr(outlook, "analyze", stale)
    text, _ = _ask(Q1)
    assert "데이터 경고" in text and "시세가 3영업일 지연(미수집) 상태라" in text and "뒤의 움직임은 알 수 없습니다" in text
    prev = {"report_id": 1, "question": Q1, "answer": text, "company_name": "SK하이닉스"}
    route["companies"] = []
    again, _ = _ask("그니까 내릴 거 같냐고 오를 거 같냐고", prev)
    assert "앞선 답과 같은 결론" in again and "데이터 경고" in again


# ---------- 10. 다른 종목으로 바꾸는 후속 질문 ----------

def test_switching_company_in_followup_uses_the_new_company(route):
    first, _ = _ask("SK하이닉스 일주일 뒤 오를까?")
    prev = {"report_id": 1, "question": "q", "answer": first, "company_name": "SK하이닉스"}
    route["companies"] = [SAMSUNG]
    text, records = _ask("삼성전자는 3개월 후 어때?", prev)
    assert text.splitlines()[0].startswith("삼성전자(005930) · 향후 60거래일 · 방향: ")
    assert "앞선 답과 같은 결론" not in text and records[0]["result"]["company_name"] == "삼성전자"
    route["companies"] = [SAMSUNG]
    text2, _ = _ask("삼성전자는 왜 올랐어? 앞으로는 어떨 거 같아?", prev)
    assert text2.splitlines()[0].startswith("삼성전자(005930) · 향후 5거래일") and "## 최근 주가 움직임과 원인" in text2


# ---------- 5(채팅). 자금 흐름 질문은 본문 검색 결과를 코드로 답한다 ----------

def test_fund_flow_question_answers_from_rag_without_llm(monkeypatch):
    calls = []

    def fake_run(name, args):
        calls.append((name, args))
        return {"found": True, "results": [
            {"type": "news", "title": "엔비디아 급락에 1061억…레버리지로 몰린 자금", "match": "content_lead", "score": 0.66,
             "published_at": "2026-11-14T08:00:00+09:00", "url": "https://x/1", "company_names": ["KODEX 200"],
             "content_snippet": "순유입 1위는 1조515억원이 들어온 'KODEX 200'이었다. 일반 지수형 상품과 레버리지 상품이 나란히"}],
            "search": {"candidates": {"news": 30, "disclosure": 0}}}

    monkeypatch.setattr(agent, "_run_tool", fake_run)
    monkeypatch.setattr(agent, "_llm_chat", lambda **k: pytest.fail("LLM을 부르지 않는다"))
    resp, records = agent._answer_fund_flow("KODEX 200 최근 자금 유입 알려줘", [KODEX])
    text = resp["answer"]
    assert calls == [("rag_search_tool", {"query": "KODEX 200 최근 자금 유입 알려줘", "company_name": "KODEX 200", "limit": 5})]
    assert "2026-11-14 \"엔비디아 급락에 1061억" in text and "\"순유입 1위는 1조515억원이 들어온 'KODEX 200'이었다.\"" in text
    assert "이 서비스가 KODEX 200의 자금 흐름을 계산한 값이 아닙니다" in text
    assert resp["used_tools"] == ["rag_search_tool"] and records


def test_fund_flow_no_result_reports_the_reason(monkeypatch):
    monkeypatch.setattr(agent, "_run_tool", lambda n, a: rag.empty_response(
        a["query"], "KODEX 200", 5, 10, {"candidates": {"news": 30, "disclosure": 0}}, {}))
    resp, _ = agent._answer_fund_flow("KODEX 200 자금 유입", [KODEX])
    assert "찾지 못했습니다" in resp["answer"] and "임베딩 데이터가 없는 것이 아닙니다" in resp["answer"]


@pytest.mark.parametrize("q,companies,handled", [
    ("KODEX 200 최근 자금 유입", [KODEX], True), ("삼성전자 외국인 순매수 기사 있어?", [SAMSUNG], True),
    ("수급이 뭐야?", [SAMSUNG], False), ("외국인 순매수 많은 종목", None, False), ("삼성전자 주가 알려줘", [SAMSUNG], False)])
def test_fund_flow_gate(monkeypatch, q, companies, handled):
    monkeypatch.setattr(agent, "_run_tool", lambda n, a: {"found": False, "message": "x"})
    assert (agent._answer_fund_flow(q, companies) is not None) == handled
