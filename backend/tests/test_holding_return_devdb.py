"""두 시점 수익률을 개발 DB의 SQL로 검산한다(수집 시세가 필요하다). LLM을 부르지 않는다."""

from datetime import date, datetime

import pytest
from sqlalchemy import text

from app import agent
from app.models import Company
from app.period_parser import KST

pytestmark = [pytest.mark.integration, pytest.mark.needs_collected_data]


def _sql(db, company_id, start, end):
    first = db.execute(text("SELECT close_price FROM stock_price WHERE company_id=:c AND price_date>=:d ORDER BY price_date LIMIT 1"), {"c": company_id, "d": start}).scalar()
    last = db.execute(text("SELECT close_price FROM stock_price WHERE company_id=:c AND price_date<=:d ORDER BY price_date DESC LIMIT 1"), {"c": company_id, "d": end}).scalar()
    return float(first), float(last), (float(last) / float(first) - 1) * 100


@pytest.mark.parametrize("ticker, start, end", [
    ("005930", date(2025, 1, 2), date(2026, 1, 2)),
    ("035420", date(2025, 3, 1), date(2025, 9, 1)),   # 시작일 토요일
    ("000270", date(2025, 5, 2), date(2025, 10, 5)),  # 종료일 일요일
    ("069500", date(2024, 1, 2), date(2024, 12, 30)),  # ETF
])
def test_matches_sql(dev_db, ticker, start, end):
    company = dev_db.query(Company).filter(Company.ticker == ticker).one()
    first, last, pct = _sql(dev_db, company.id, start, end)
    answer, result = agent._holding_part(company, start, end, datetime.now(KST))
    assert f"{first:,.0f}원" in answer and f"{last:,.0f}원" in answer and f"{pct:+.2f}%" in answer
    assert result["period_change_pct"] == pytest.approx(pct, abs=0.01)


def test_chat_path_answers_without_llm(dev_db, monkeypatch):
    company = dev_db.query(Company).filter(Company.ticker == "005930").one()
    monkeypatch.setattr(agent, "_llm_chat", lambda **k: pytest.fail("LLM을 부르면 안 된다"))
    question = "삼성전자를 2025년 1월 2일에 사서 2026년 1월 2일까지 들고 있었으면 수익률이 얼마야?"
    response, records = agent._answer_holding_return(question, [company])
    assert "140.64%" in response["answer"] and records[0]["tool_name"] == "stock_tool"


def test_out_of_range_is_refused(dev_db):
    company = dev_db.query(Company).filter(Company.ticker == "005930").one()
    answer, result = agent._holding_part(company, date(2022, 1, 3), date(2024, 1, 3), datetime.now(KST))
    assert "2023-10-30" in answer and "period_change_pct" not in result
