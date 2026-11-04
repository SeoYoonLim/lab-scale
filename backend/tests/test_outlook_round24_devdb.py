"""2026-11-04(24차)를 실제 dev DB로 확인한다: 잠정실적(SK하이닉스 2026년 3분기, 접수번호 20261028800036)이 최신 실적으로 나오고
출처·발표일이 맞는지, 후속 결론 답이 현재 판단을 먼저 쓰는지(평가기 24차 절로 검산), 시세 상태 분류, 전 종목 잠정치 점검.
batch 20(holdout) 문항과 같은 질문은 쓰지 않는다. 전망 경로는 LLM을 부르면 실패한다."""

from datetime import datetime

import pytest

from app import agent, earnings, outlook
from app.briefing import KST
from app.models import Company
from evals import forecast_check as FC

pytestmark = pytest.mark.integration


@pytest.fixture
def no_llm(monkeypatch):
    def boom(**kw):
        raise AssertionError("이 경로에서 LLM을 부르면 안 된다")

    monkeypatch.setattr(agent, "_llm_chat", boom)


def _company(db, name):
    return db.query(Company).filter(Company.name == name).one()


@pytest.mark.needs_collected_data
def test_skhynix_latest_quarter_is_the_q3_preliminary_and_q2_is_final(dev_db):
    rows = earnings.recent_for_answer(dev_db, _company(dev_db, "SK하이닉스"))
    if not rows or rows[0]["source"] != "preliminary":
        pytest.skip("SK하이닉스 3분기 잠정실적이 없거나 3분기 보고서가 이미 수집된 DB")
    q3, q2 = rows[0], rows[1]
    assert (q3["period_label"], q3["rcept_no"], q3["disclosed_date"], q3["basis"]) == (
        "2026년 3분기", "20261028800036", "2026-10-28", "연결")
    assert q3["revenue"] == 104_615_483e6 and q3["operating_income"] == 83_333_818e6 and q3["net_income"] == 39_775_593e6
    assert q2["source"] == "final" and q2["period_label"] == "2026년 2분기"
    for r in rows:
        assert FC.check_earnings_line24(dev_db, "- " + outlook.earnings_row_text(r), "SK하이닉스") == []


@pytest.mark.needs_collected_data
@pytest.mark.parametrize("q,kind", [("결론이 뭐야 올라 내려?", "verdict"), ("하나 고르라면?", "pick"),
                                    ("상승 쪽이랑 하락 쪽 중 뭐가 더 세?", "compare"), ("그럼 사도 된다는 거야?", "buy"),
                                    ("오를 가능성이 더 커?", "probability")])
def test_followups_state_the_judgment_first(dev_db, no_llm, q, kind):
    r1 = agent.ask_question("삼성전자 다음 주 전망 분석해줘", save_report=False)
    prev = {"report_id": None, "question": "x", "answer": r1["answer"], "company_name": "삼성전자"}
    assert FC.check_judgment24(dev_db, r1["answer"], "삼성전자", "1주일", "standalone") == []
    r2 = agent.ask_question(q, save_report=False, previous=prev)
    assert FC.check_judgment24(dev_db, r2["answer"], "삼성전자", "1주일", kind) == []


@pytest.mark.needs_collected_data
def test_earnings_and_data_followups(dev_db, no_llm):
    r1 = agent.ask_question("삼성전자 다음 주 전망 분석해줘", save_report=False)
    prev = {"report_id": None, "question": "x", "answer": r1["answer"], "company_name": "삼성전자"}
    e = agent.ask_question("실적 반영해도 판단이 같아?", save_report=False, previous=prev)["answer"]
    assert FC.check_judgment24(dev_db, e, "삼성전자", "1주일", "earnings") == []
    assert FC.check_earnings24(dev_db, e, "삼성전자") == []
    d = agent.ask_question("시세가 최신이 아니면 어떻게 돼?", save_report=False, previous=prev)["answer"]
    assert FC.check_data24(dev_db, d, "삼성전자") == []
    assert outlook.marker_of(d)["name"] == "삼성전자"


@pytest.mark.needs_collected_data
def test_price_status_is_one_of_the_kinds(dev_db):
    c = _company(dev_db, "SK하이닉스")
    from sqlalchemy import func

    from app.models import StockPrice
    base = dev_db.query(func.max(StockPrice.price_date)).filter(StockPrice.company_id == c.id).scalar()
    now = datetime.now(KST)
    st = outlook.price_status(dev_db, base, now)
    assert st["status"] in ("최신", "미수집", "수집 실패", "휴장일 추정", "종목만 누락")
    assert st["status"] == FC.status24(dev_db, "SK하이닉스", base.isoformat(), f"{now:%Y-%m-%d %H:%M}")[1]


@pytest.mark.needs_collected_data
def test_preliminary_rules_keep_basis_and_cumulative_consistent(dev_db):
    from scripts.check_preliminary import scan

    r = scan(dev_db)
    if r["compared_quarters"] < 50:
        pytest.skip("잠정실적과 정기보고서가 겹치는 분기가 적은 DB")
    # 2026-11-04: 기준을 가리지 않으면 불일치 15개 분기, 답변 규칙으로는 186개 중 4건(영업이익 1~4%, 감사 뒤 수정)
    assert len(r["mismatches"]) <= r["compared_quarters"] * 0.05
    assert r["naive_mismatch_quarters"] > len(r["mismatches"])
