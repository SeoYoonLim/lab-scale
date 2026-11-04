"""전망·후속 결론 경로를 실제 dev DB로 확인한다(app/outlook.py). LLM은 부르지 않는다(부르면 실패).

- 대화 이력: API와 같은 방식(직전 리포트를 reports.get_report로 읽어 previous로 넘긴다)으로 3턴을 이어 묻고, 직전 종목·기간이 유지되는지
- 숫자: 답변의 5거래일 수익률·5일 이동평균을 원시 SQL로 다시 계산해 대조한다
- 근거: 전망 답변의 근거 뉴스·공시가 모두 요청 종목인지(SK하이닉스 질문에 삼성전자·삼성전기 기사 없음)
"""

import re

import pytest
from sqlalchemy import text

from app import agent, outlook
from app.reports import get_report


@pytest.fixture
def no_llm(monkeypatch):
    def boom(**kw):
        raise AssertionError("전망·후속 결론 질문에 LLM을 부르면 안 된다")

    monkeypatch.setattr(agent, "_llm_chat", boom)


@pytest.mark.integration
@pytest.mark.needs_collected_data
def test_three_turn_followup_keeps_company_and_period(make_user, dev_db, no_llm):
    me, _ = make_user()
    r1 = agent.ask_question("SK하이닉스 한 달 안에 오를지 내릴지 분석해줘", user_id=me.id)
    assert outlook.marker_of(r1["answer"]) == {"name": "SK하이닉스", "ticker": "000660", "period": "1개월"}
    prev = get_report(r1["report_id"], user_id=me.id)
    assert prev["company_name"] == "SK하이닉스"  # 저장된 리포트가 종목을 기억한다(다음 후속 질문이 이어받는 값)

    r2 = agent.ask_question("그래서 결론이 뭐야? 올라? 아니면 내려가?", user_id=me.id, previous=prev)
    assert "용어 사전" not in r2["answer"] and outlook.marker_of(r2["answer"])["period"] == "1개월"
    prev = get_report(r2["report_id"], user_id=me.id)
    assert prev["company_name"] == "SK하이닉스"

    r3 = agent.ask_question("아니 니 생각에 오를거 같냐 내릴거 같냐", user_id=me.id, previous=prev)
    assert outlook.marker_of(r3["answer"]) == {"name": "SK하이닉스", "ticker": "000660", "period": "1개월"}
    assert r3["previous_report_id"] == r2["report_id"]
    for r in (r1, r2, r3):
        assert all(s["company_names"] == ["SK하이닉스"] for s in r["sources"])


@pytest.mark.integration
@pytest.mark.needs_collected_data
def test_forecast_numbers_match_sql(dev_db, no_llm):
    r = agent.ask_question("삼성전자 일주일 안에 오를까?", save_report=False)
    base = re.search(r"기준일\(최신 거래일\): (\d{4}-\d{2}-\d{2})", r["answer"]).group(1)
    closes = [float(c) for (c,) in dev_db.execute(text(
        "SELECT p.close_price FROM stock_price p JOIN company c ON c.id = p.company_id "
        "WHERE c.name = '삼성전자' AND p.price_date <= :d ORDER BY p.price_date DESC LIMIT 6"), {"d": base})]
    ret5 = round((closes[0] / closes[5] - 1) * 100, 2)
    assert f"5거래일 수익률: {ret5:+.2f}%" in r["answer"]
    ma5 = round(sum(closes[:5]) / 5)
    assert f"5일 이동평균: {ma5:,.0f}원" in r["answer"]


@pytest.mark.integration
@pytest.mark.needs_collected_data
def test_forecast_evidence_is_only_the_requested_company(dev_db, no_llm):
    from app.news_relevance import title_mentions

    r = agent.ask_question("SK하이닉스 일주일 안에 오를지 내릴지 분석해줘", save_report=False)
    assert r["used_tools"][0] == "stock_tool"
    for s in r["sources"]:
        assert s["company_names"] == ["SK하이닉스"]
        if s["type"] == "news":
            assert title_mentions("SK하이닉스", s["title"])  # 별칭(하이닉스·하닉) 포함, 2026-11-03


@pytest.mark.integration
@pytest.mark.needs_collected_data
def test_news_tool_in_chat_path_uses_the_same_filter(dev_db):
    from app.news_relevance import title_mentions
    from app.tools.news_tool import news_tool

    out = news_tool("SK하이닉스", limit=5)
    assert out["found"] and all(title_mentions("SK하이닉스", n["title"]) for n in out["news"])


@pytest.mark.integration
def test_order_sentence_does_not_take_the_forecast_path(dev_db, monkeypatch):
    called = []
    monkeypatch.setattr(agent, "_answer_forecast", lambda q, p: called.append(q))
    monkeypatch.setattr(agent, "_llm_chat", lambda **kw: {"message": {"content": "모의 주문은 주문 화면에서 할 수 있습니다."}})
    agent.ask_question("삼성전자 10주 사줘", save_report=False)
    assert outlook.is_order_request("삼성전자 10주 사줘") and agent._forecast_subject("삼성전자 10주 사줘", None) is None


@pytest.mark.integration
@pytest.mark.needs_collected_data
def test_stale_prices_give_hold_with_real_data(dev_db):
    """마지막 시세일에서 5영업일을 넘긴 시점이면 판단 유보와 데이터 부족 경고, 1~5영업일이면 경고만 붙이고 계산한다."""
    from datetime import datetime, timedelta

    from app.briefing import KST
    from app.models import Company, StockPrice
    from sqlalchemy import func

    company = dev_db.query(Company).filter(Company.name == "삼성전자").one()
    last = dev_db.query(func.max(StockPrice.price_date)).filter(StockPrice.company_id == company.id).scalar()
    period = outlook.PERIODS["1주일"]
    late = datetime.combine(last + timedelta(days=14), datetime.min.time(), KST).replace(hour=18)
    res = outlook.analyze(dev_db, company, period, late)
    assert res["label"] == "판단 유보" and res["data"]["hold"] and res["data"]["lag_business_days"] > outlook.MAX_LAG_BUSINESS_DAYS
    text = outlook.forecast_answer(company, period, res, [], [], None, late)
    assert "판단 유보" in text.splitlines()[2] and "데이터가 부족합니다" in text  # 0: 제목 줄, 2: 상태 줄(2026-11-04)
    fresh = datetime.combine(last + timedelta(days=1), datetime.min.time(), KST).replace(hour=18)
    res = outlook.analyze(dev_db, company, period, fresh)
    assert not res["data"]["hold"] and res["label"] != "판단 유보"
