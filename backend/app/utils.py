import json


def extract_answer_text(content: str) -> str:
    """LLM 응답이 {"name": "answer", "parameters": {...}} 형태의 JSON으로 오는
    경우, parameters 안의 실제 답변 텍스트를 뽑아낸다.

    모델이 parameters 안에 쓰는 키 이름이 매번 달라서(content/answer/basic_terms 등)
    키 이름과 무관하게 값 기준으로 처리한다:
    - parameters의 값이 정확히 1개면 그 값을 그대로 반환
    - 여러 개면 그중 문자열 값들을 공백으로 이어붙여 반환
    - JSON이 아니거나 parameters가 없거나 문자열 값이 하나도 없으면 원본 content 반환
    """
    try:
        parsed = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        return content

    if not isinstance(parsed, dict):
        return content

    params = parsed.get("parameters")
    if not isinstance(params, dict) or not params:
        return content

    values = list(params.values())
    if len(values) == 1:
        return values[0]

    string_values = [v for v in values if isinstance(v, str)]
    if string_values:
        return " ".join(string_values)

    return content
