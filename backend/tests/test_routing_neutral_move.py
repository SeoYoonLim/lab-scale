"""2026-11-18 최종 리허설: 방향 없는 등락 표현·'배경'도 등락 원인 질문이다(LLM 경로로 가서 기준일 뒤 기사로 '상승'을 지어냈다)."""
import pytest

from app.answer_cause import is_cause_only_question
from app.routing import is_cause_question


@pytest.mark.parametrize("q", [
    "삼성전자 최근 5거래일 동안 왜 움직였어?",
    "삼성전자 왜 움직였어?",
    "산일전기 최근 주가가 움직인 이유가 뭐야?",
    "현대차 최근 주가 움직임의 원인을 설명해줘",
    "삼성전자 최근 주가 변동 원인은?",
    "삼성전자 등락 이유가 뭐야?",
    "셀트리온 최근 주가 움직임의 배경이 뭐야?",
    "삼성전자 주가가 출렁인 이유는?",
])
def test_neutral_move_with_cause_word_is_cause_question(q):
    assert is_cause_question(q)


@pytest.mark.parametrize("q", [
    "KODEX 레버리지 변동성 위험 요인을 설명해줘",   # 변동성은 등락이 아니다
    "삼성전자 최근 종가와 등락률 알려줘",           # 원인 표현이 없다
    "뉴스랑 주가 움직임 같이 알려줘",
    "PER이 왜 중요해?",
    "이 회사 설립 배경이 뭐야?",                   # 배경만 있고 등락 표현이 없다
])
def test_not_cause_question(q):
    assert not is_cause_question(q)


def test_event_reaction_and_forecast_stay_on_their_paths():
    # 사건 뒤 반응은 사건 반응 경로(원인 코드 답이 아니다)
    assert not is_cause_only_question("SK하이닉스 최근 공시 이후 주가 어떻게 움직였어?")
    # 종목 하나·기간 없음이면 원인 단독 코드 답
    assert is_cause_only_question("삼성전자 왜 움직였어?")
