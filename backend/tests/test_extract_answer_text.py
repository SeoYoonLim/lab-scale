"""호출 JSON의 인자 값을 답으로 뽑지 않는지(app/utils.extract_answer_text). DB·Ollama 없이 돈다."""

import pytest

from app.utils import extract_answer_text


@pytest.mark.parametrize("content", [
    '{"name": "stock_tool", "parameters": {"ticker": "기아"}}',
    '{"name": "news_tool", "parameters": {"company_name": "기아", "limit": 5}}',
])
def test_tool_call_json_is_not_turned_into_an_argument_value(content):
    assert extract_answer_text(content) == content


def test_tool_call_json_reaches_the_tool_call_check():
    from app import agent

    content = '{"name": "stock_tool", "parameters": {"ticker": "기아"}}'
    assert agent._mentions_tool_call(extract_answer_text(content))


def test_answer_json_is_still_extracted():
    assert extract_answer_text('{"name": "answer", "parameters": {"content": "기아는 자동차 회사입니다."}}') == "기아는 자동차 회사입니다."
    assert extract_answer_text("그냥 글입니다.") == "그냥 글입니다."
