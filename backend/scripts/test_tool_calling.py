"""
Ollama tool calling 검증 스크립트

목적: qwen2.5:7b-instruct 모델이 실제로 "함수(tool)를 선택해서 호출"할 수 있는지
      최소 구조로 확인한다. (AI 투자 리서치 에이전트의 핵심 리스크 검증)

사전 준비:
1) Ollama 설치: https://ollama.com/download
2) 모델 받기:   ollama pull qwen2.5:7b-instruct
3) 파이썬 패키지: pip install ollama

실행:
    python test_tool_calling.py
"""

import json
import ollama

MODEL_NAME = "qwen2.5:7b-instruct"


# ------------------------------------------------------------
# 1. 가짜(mock) stock_tool 정의
#    실제 프로젝트에서는 이 함수 안에서 pykrx 등을 호출해서
#    진짜 주가 데이터를 가져오게 됩니다. 지금은 tool calling
#    구조 자체가 동작하는지만 확인하는 단계라 더미 데이터로 대체.
# ------------------------------------------------------------
def stock_tool(ticker: str, period_days: int = 1) -> dict:
    """지정한 종목의 최근 N일 등락률/거래량을 조회한다 (mock)."""
    fake_db = {
        "삼성전자": {"change_pct": 5.2, "volume": 25_000_000},
        "SK하이닉스": {"change_pct": -2.1, "volume": 8_500_000},
    }
    data = fake_db.get(ticker, {"change_pct": 0.0, "volume": 0})
    return {
        "ticker": ticker,
        "period_days": period_days,
        "change_pct": data["change_pct"],
        "volume": data["volume"],
    }


# Ollama에게 알려줄 tool 스펙 (OpenAI function calling과 동일한 형식)
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "stock_tool",
            "description": "특정 종목의 최근 N일간 주가 등락률과 거래량을 조회한다.",
            "parameters": {
                "type": "object",
                "properties": {
                    "ticker": {
                        "type": "string",
                        "description": "종목명 (예: 삼성전자, SK하이닉스)",
                    },
                    "period_days": {
                        "type": "integer",
                        "description": "조회할 기간(일). 기본값 1.",
                    },
                },
                "required": ["ticker"],
            },
        },
    }
]

AVAILABLE_FUNCTIONS = {"stock_tool": stock_tool}


def ask(question: str):
    print(f"\n{'='*60}\n질문: {question}\n{'='*60}")

    messages = [{"role": "user", "content": question}]

    # 1차 호출: 모델이 tool을 쓸지 말지 스스로 판단
    response = ollama.chat(
        model=MODEL_NAME,
        messages=messages,
        tools=TOOLS,
    )

    msg = response["message"]
    tool_calls = msg.get("tool_calls")

    if not tool_calls:
        # 모델이 tool 없이 바로 답한 경우
        print("[Tool 호출 없음]")
        print("최종 답변:", msg["content"])
        return

    print(f"[Tool 호출 감지] {len(tool_calls)}건")
    messages.append(msg)

    # 2. 모델이 요청한 tool을 실제로 실행하고 결과를 다시 넘겨줌
    for call in tool_calls:
        fn_name = call["function"]["name"]
        fn_args = call["function"]["arguments"]
        print(f"  -> {fn_name}({fn_args})")

        if fn_name not in AVAILABLE_FUNCTIONS:
            result = {"error": f"unknown tool: {fn_name}"}
        else:
            result = AVAILABLE_FUNCTIONS[fn_name](**fn_args)

        print(f"     결과: {result}")

        messages.append(
            {
                "role": "tool",
                "content": json.dumps(result, ensure_ascii=False),
            }
        )

    # 3. tool 결과를 반영한 최종 답변 생성
    final = ollama.chat(model=MODEL_NAME, messages=messages)
    print("\n최종 답변:", final["message"]["content"])


if __name__ == "__main__":
    # 케이스 1: tool 호출이 필요한 질문
    ask("삼성전자가 오늘 왜 올랐는지 최근 데이터로 확인해줘.")

    # 케이스 2: tool 호출 없이도 답할 수 있는 일반 질문
    # (모델이 불필요하게 tool을 남발하지 않는지 확인하는 목적)
    ask("주식 투자를 처음 시작할 때 알아야 할 기본 용어를 알려줘.")
