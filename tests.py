"""
Ollama + qwen2.5:7b-instruct Tool Calling 검증 스크립트

목적: "질문 -> 모델이 알맞은 tool을 스스로 선택해서 호출 -> tool 결과를 반영해서
최종 답변 생성"까지 전체 루프가 실제로 도는지 확인한다.

사전 준비:
  1) ollama 설치: https://ollama.com/download
  2) 모델 받기:   ollama pull qwen2.5:7b-instruct
  3) ollama 서버 실행 확인 (설치하면 보통 백그라운드로 자동 실행됨)
  4) pip install requests

실행:
  python test_tool_calling.py
"""

import json
import requests

OLLAMA_URL = "http://localhost:11434/api/chat"
MODEL = "qwen2.5:7b-instruct"


# ---------------------------------------------------------------------------
# 1. 가짜 stock_tool 정의 (실제 PRD의 stock_tool을 흉내낸 더미 함수)
# ---------------------------------------------------------------------------
def get_stock_price(ticker: str, date: str = "latest") -> dict:
    """실제로는 pykrx를 호출할 자리. 지금은 tool calling 자체만 검증하는 게
    목적이라 하드코딩된 값을 반환한다."""
    fake_db = {
        "005930": {"name": "삼성전자", "price": 71500, "change_pct": 2.3},
        "000660": {"name": "SK하이닉스", "price": 182000, "change_pct": -1.1},
    }
    return fake_db.get(ticker, {"error": f"'{ticker}' 데이터를 찾을 수 없음"})


# 모델에게 알려줄 tool 스펙 (OpenAI function-calling과 동일한 포맷을 Ollama도 지원)
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_stock_price",
            "description": "국내 주식 종목의 현재가와 등락률을 조회한다.",
            "parameters": {
                "type": "object",
                "properties": {
                    "ticker": {
                        "type": "string",
                        "description": "종목 코드 (예: 삼성전자=005930, SK하이닉스=000660)",
                    },
                    "date": {
                        "type": "string",
                        "description": "조회 날짜 (YYYY-MM-DD) 또는 'latest'",
                    },
                },
                "required": ["ticker"],
            },
        },
    }
]

AVAILABLE_FUNCTIONS = {"get_stock_price": get_stock_price}


def chat(messages: list) -> dict:
    resp = requests.post(
        OLLAMA_URL,
        json={
            "model": MODEL,
            "messages": messages,
            "tools": TOOLS,
            "stream": False,
        },
        timeout=120,
    )
    resp.raise_for_status()
    return resp.json()


def run_test(question: str):
    print(f"\n{'='*60}")
    print(f"질문: {question}")
    print(f"{'='*60}")

    messages = [
    {"role": "system", "content": "당신은 한국어로만 답변하는 금융 리서치 어시스턴트입니다. 다른 언어를 절대 섞지 마세요."},
    {"role": "user", "content": question},
]

    # 1차 호출: 모델이 tool을 부를지 판단
    result = chat(messages)
    assistant_msg = result["message"]
    print("\n[1차 응답]")
    print(json.dumps(assistant_msg, ensure_ascii=False, indent=2))

    tool_calls = assistant_msg.get("tool_calls")
    if not tool_calls:
        print("\n⚠️  Tool call이 발생하지 않았습니다. (모델이 그냥 텍스트로만 답함)")
        print("최종 답변:", assistant_msg.get("content"))
        return False

    # 2. tool 호출 내역을 대화에 추가하고, 실제 함수를 실행해서 결과를 넣어준다
    messages.append(assistant_msg)
    for call in tool_calls:
        fn_name = call["function"]["name"]
        fn_args = call["function"]["arguments"]
        if isinstance(fn_args, str):
            fn_args = json.loads(fn_args)

        print(f"\n[호출된 Tool] {fn_name}({fn_args})")

        fn = AVAILABLE_FUNCTIONS.get(fn_name)
        tool_result = fn(**fn_args) if fn else {"error": "정의되지 않은 tool"}
        print(f"[Tool 실행 결과] {tool_result}")

        messages.append(
            {
                "role": "tool",
                "content": json.dumps(tool_result, ensure_ascii=False),
            }
        )

    # 3차 호출: tool 결과를 반영한 최종 답변 생성
    final = chat(messages)
    print("\n[최종 답변]")
    print(final["message"]["content"])
    return True


if __name__ == "__main__":
    # 케이스 1: 반드시 tool을 호출해야 하는 질문
    ok1 = run_test("삼성전자(005930) 지금 주가 얼마야?")

    # 케이스 2: tool이 필요 없는 일반 질문 (tool을 안 부르는 게 정상)
    ok2 = run_test("리서치 보고서란 무엇인지 한 문장으로 설명해줘.")

    print(f"\n{'='*60}")
    print("결과 요약")
    print(f"{'='*60}")
    print(f"- 주가 질문에서 tool 호출: {'성공' if ok1 else '실패 (재확인 필요)'}")
    print(f"- 일반 질문에서 tool 미호출(정상): {'예상대로' if not ok2 else '모델이 불필요하게 tool을 부름'}")