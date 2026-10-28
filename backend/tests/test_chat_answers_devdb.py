"""(B, D) 채팅 코드 답변을 실제 dev DB로 확인한다. LLM은 부르지 않는다(부르면 실패).

- 채팅 브리핑: 로그인 사용자 본인의 보유·관심종목만 쓰고, 리포트·tool_call_log("briefing")가 저장된다. 시세 없이도 돈다(CI 포함).
- 기간 수익률 템플릿: 답변 숫자를 DB에서 원시 SQL로 다시 계산해 대조한다(수집 시세 필요).
"""

from datetime import datetime

import pytest
from sqlalchemy import text

from app import agent
from app.briefing import KST, session_is_open
from app.models import Holding, ResearchReport, ToolCallLog, VirtualAccount, Watchlist


@pytest.fixture
def no_llm(monkeypatch):
    def boom(**kw):
        raise AssertionError("코드 답변 질문에 LLM을 부르면 안 된다")

    monkeypatch.setattr(agent, "_llm_chat", boom)


@pytest.mark.integration
def test_chat_briefing_uses_only_the_users_own_data(make_user, dev_db, no_llm):
    me, _ = make_user()
    other, _ = make_user()
    (mine_id, mine), (theirs_id, theirs) = dev_db.execute(text("SELECT id, name FROM company ORDER BY id LIMIT 2")).all()
    dev_db.add(Watchlist(device_id=me.owner_key, company_id=mine_id))
    dev_db.add(VirtualAccount(device_id=other.owner_key, cash_balance=1_000_000))
    dev_db.flush()
    dev_db.add(Holding(device_id=other.owner_key, company_id=theirs_id, quantity=1, avg_price=1000))
    dev_db.commit()

    r = agent.ask_question("오늘 브리핑 해줘", user_id=me.id)
    assert mine in r["answer"] and theirs not in r["answer"]
    assert "모의투자 계좌가 아직 없습니다" in r["answer"]  # 읽기 전용: 계좌를 만들지 않는다
    dev_db.rollback()
    assert dev_db.query(VirtualAccount).filter(VirtualAccount.device_id == me.owner_key).count() == 0
    report = dev_db.get(ResearchReport, r["report_id"])
    assert report.user_id == me.id
    assert [t.tool_name for t in dev_db.query(ToolCallLog).filter(ToolCallLog.report_id == r["report_id"])] == ["briefing"]

    anonymous = agent.ask_question("내 보유종목 오늘 어때?", save_report=False)
    assert anonymous["answer"] == "로그인하면 개인화 브리핑을 볼 수 있습니다."


@pytest.mark.integration
@pytest.mark.needs_collected_data
def test_period_return_template_matches_db(dev_db, no_llm):
    from app.period_parser import parse_period

    q = "삼성전자 최근 한 달 수익률 알려줘"
    r = agent.ask_question(q, save_report=False)
    start = parse_period(q).start_date
    rows = dev_db.execute(text(
        "SELECT p.price_date, p.close_price FROM stock_price p JOIN company c ON c.id = p.company_id "
        "WHERE c.name = '삼성전자' ORDER BY p.price_date DESC")).all()
    now = datetime.now(KST)
    if session_is_open(now):
        rows = [x for x in rows if x.price_date != now.date()]
    last = rows[0]
    base = next(x for x in rows if x.price_date < start)
    pct = (last.close_price / base.close_price - 1) * 100
    assert f"{last.close_price:,.0f}원" in r["answer"] and f"{base.close_price:,.0f}원" in r["answer"]
    assert f"{pct:+.2f}%" in r["answer"]
    assert r["used_tools"] == ["stock_tool"]
