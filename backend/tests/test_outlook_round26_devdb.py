"""2026-11-04 밤(26차)을 실제 dev DB로 확인한다: SK하이닉스 잠정실적 공시(20261028800036)가 상세 답 어디서나 2026-10-28이고,
간결·상세 답의 방향·근거가 같고, 종목명 없는 "전체적으로 자세히 분석해 줘"가 직전 종목의 상세 리포트이며, 저장된 공시 날짜가
접수번호와 맞는지. batch 24(holdout)와 같은 질문은 쓰지 않는다. 전망 경로는 LLM을 부르면 실패한다."""

import re

import pytest
from sqlalchemy import text

from app import agent
from evals import forecast_check as FC

pytestmark = pytest.mark.integration


@pytest.fixture
def no_llm(monkeypatch):
    def boom(**kw):
        raise AssertionError("이 경로에서 LLM을 부르면 안 된다")

    monkeypatch.setattr(agent, "_llm_chat", boom)


@pytest.mark.needs_collected_data
def test_preliminary_filing_date_is_the_same_everywhere(dev_db, no_llm):
    a = agent.ask_question("SK하이닉스 다음 주 전망 리포트로 보여줘", save_report=False)["answer"]
    if "20261028800036" not in a:
        pytest.skip("SK하이닉스 3분기 잠정실적이 최신 실적이 아닌 DB")
    assert FC.check_dates26(dev_db, a, "SK하이닉스") == []
    dates = set(re.findall(r"(\d{4}-\d{2}-\d{2})[^\n]{0,8}\"연결재무제표기준영업\(잠정\)실적\(공정공시\)\"", a))
    dates |= set(re.findall(r"접수·발표일 (\d{4}-\d{2}-\d{2}), DART 접수번호 20261028800036", a))
    assert dates == {"2026-10-28"}


@pytest.mark.needs_collected_data
@pytest.mark.parametrize("name,q", [("SK하이닉스", "SK하이닉스 이번 주 방향 어때?"), ("KT&G", "KT&G 한 달 뒤엔 오를까?")])
def test_brief_and_detail_agree(dev_db, no_llm, name, q):
    brief = agent.ask_question(q, save_report=False)["answer"]
    prev = {"report_id": None, "question": q, "answer": brief, "company_name": name}
    detail = agent.ask_question("자세하게 분석해서 보여줘", save_report=False, previous=prev)["answer"]
    assert "## " not in brief and "## 기술적 지표" in detail
    assert brief.splitlines()[0] == detail.splitlines()[0]  # 같은 방향·신뢰도
    assert FC.check_consistency26(detail, [brief]) == []
    assert FC.check_evidence26(brief, "brief") == [] and FC.check_evidence26(detail, "detail") == []


@pytest.mark.needs_collected_data
def test_stored_disclosure_dates_match_the_receipt_numbers(dev_db):
    row = dev_db.execute(text(
        "select count(*), count(*) filter (where substring(source_url from 'rcptNo=(\\d{8})') "
        "<> to_char(disclosed_at at time zone 'Asia/Seoul', 'YYYYMMDD')) from disclosure where source_url ~ 'rcptNo=\\d{14}'")).one()
    if row[0] < 1000:
        pytest.skip("공시가 적은 DB")
    # 2026-11-04: 8,722건 중 5건(정정·주요사항보고서)만 수집 접수일이 접수번호 날짜보다 하루 늦다. 답변은 접수번호 날짜를 쓴다.
    assert row[1] <= row[0] * 0.001
