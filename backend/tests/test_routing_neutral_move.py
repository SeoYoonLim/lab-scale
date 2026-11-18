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


def test_return_and_cause_together_is_cause_question_only_when_adjacent():
    assert is_cause_question("삼성전자와 SK하이닉스의 기간별 수익률과 원인을 비교해 줘.")
    assert not is_cause_question("삼성전자 수익률이 왜 중요해?")
    assert not is_cause_question("ETF 수익률과 지수 수익률이 왜 달라?")


def test_tool_name_leak_sentence_is_removed_even_with_korean_particle_or_case():
    from app.answer_tidy import strip_generic
    answer = ("## 주가 동향\nSK하이닉스의 최근 주가는 1,098,000원으로, 20거래일간 -10.22%입니다.\n\n## 뉴스·공시 근거\n"
              "SK하이닉스의 주가 변동 원인은 news_tool에 저장된 뉴스 중에서 관련된 뉴스가 없어서 Disclosure_tool에 저장된 공시 중에서 "
              "관련된 내용이 없어서, RAG_search_tool을 호출하여 검색 결과가 없어서, 자세한 변동 원인은 알 수 없습니다.")
    text, removed = strip_generic(answer)
    assert removed == 1 and "_tool" not in text.lower() and "-10.22%" in text
    # 도구 이름이 아닌 영문 낱말(예: stock_tools_guide 같은 다른 식별자)은 건드리지 않는다
    assert strip_generic("참고: mystock_toolbox 문서")[1] == 0
