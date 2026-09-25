"""Ollama(llama3.1:8b)를 실제로 호출하는 에이전트 테스트. 느리고 비결정적이라 기본 실행에서 빠진다.

실행: pytest -m slow
단발 실행이라 성공률 측정이 아니라 "완전히 망가지지 않았는지"만 본다. 성공률 비교는
scripts/benchmark_models.py로 한다.
"""

import pytest

from app.agent import ask_question

pytestmark = [pytest.mark.slow, pytest.mark.integration]

FEWSHOT_LEAKS = ["롯데칠성", "종목을 찾지 못했습니다"]


def test_concept_question_answers_in_korean():
    r = ask_question("공매도가 뭔지 짧게 설명해줘.", save_report=False)
    assert r["answer"].strip()
    assert r["report_id"] is None


def test_stock_question_calls_stock_tool_without_fewshot_leak():
    r = ask_question("삼성전자 최근 3일 등락률 알려줘.", save_report=False)
    assert "stock_tool" in r["used_tools"]
    assert not any(s in r["answer"] for s in FEWSHOT_LEAKS)


def test_multi_question_leaves_no_fewshot_leak():
    r = ask_question("KCC 오늘 주가랑 관련 뉴스 같이 확인해줘.", save_report=False)
    assert r["used_tools"]
    assert not any(s in r["answer"] for s in FEWSHOT_LEAKS)
