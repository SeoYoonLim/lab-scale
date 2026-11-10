"""2026-11-04(25차)를 실제 dev DB로 확인한다: SK하이닉스 2026-11-03은 국면이 반등 시도여도 방향이 상승 쪽이고(국면이 방향을 덮어쓰지
않는다), 같은 대화의 후속 답(판단·고르기·이유·한 줄)이 방향을 바꾸지 않는다. batch 22(holdout)와 같은 질문은 쓰지 않는다. LLM을 부르면 실패한다."""

import pytest

from app import agent, outlook
from evals import forecast_check as FC

pytestmark = pytest.mark.integration


@pytest.fixture
def no_llm(monkeypatch):
    def boom(**kw):
        raise AssertionError("이 경로에서 LLM을 부르면 안 된다")

    monkeypatch.setattr(agent, "_llm_chat", boom)


@pytest.mark.needs_collected_data
def test_skhynix_direction_is_up_although_the_regime_is_a_rebound(dev_db, no_llm):
    a = agent.ask_question("SK하이닉스 다음 주 방향 분석해줘", save_report=False)["answer"]
    if "기준일 2026-11-03 종가" not in a:
        pytest.skip("기준일이 2026-11-03이 아닌 DB(시세가 더 쌓였다)")
    lines = [x for x in a.splitlines() if x.strip()]
    assert lines[0].startswith("SK하이닉스(000660) · 향후 5거래일 · 방향: 상승 쪽(신뢰도 낮음)")
    assert "현재는 단기 반등 시도, 추세 전환은 미확인 (기준일 2026-11-03 종가)" in a
    assert FC.check_direction25(dev_db, a, "SK하이닉스", "1주일", "standalone") == []


@pytest.mark.needs_collected_data
def test_followups_keep_the_same_direction(dev_db, no_llm):
    r1 = agent.ask_question("삼성바이오로직스 일주일 전망 분석해줘", save_report=False)
    prev = {"report_id": None, "question": "x", "answer": r1["answer"], "company_name": "삼성바이오로직스"}
    answers = [r1["answer"]]
    for q, kind in (("그래서 결론은 오른다는 거야?", "verdict"), ("하나 고르라면?", "pick"), ("왜 그렇게 봤어?", "why"),
                    ("한 줄로 정리해", "oneline")):
        a = agent.ask_question(q, save_report=False, previous=prev)["answer"]
        assert FC.check_direction25(dev_db, a, "삼성바이오로직스", "1주일", kind) == [], (q, a)
        assert FC.check_stable25(a, answers, "1주일") == [], q
        answers.append(a)
        prev = {"report_id": None, "question": q, "answer": a, "company_name": "삼성바이오로직스"}
    assert outlook.marker_of(answers[-1])["name"] == "삼성바이오로직스"
