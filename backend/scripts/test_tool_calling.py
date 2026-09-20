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
import os
import sys

import ollama

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.tools.news_tool import news_tool  # noqa: E402
from app.tools.stock_tool import stock_tool  # noqa: E402

MODEL_NAME = "llama3.1:8b"


# Ollama에게 알려줄 tool 스펙 (OpenAI function calling과 동일한 형식)
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "stock_tool",
            "description": (
                "DB에 저장된 실제 데이터 기준으로, 특정 종목의 최근 N일간 주가"
                "(등락률, 거래량, 종가 등)를 조회한다. 아직 pykrx로 수집되지 않은"
                "종목이거나 데이터가 없는 경우 found=false와 함께 그 사유를 담은"
                "message를 반환한다."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "ticker": {
                        "type": "string",
                        "description": (
                            "종목코드가 아니라 종목명(예: 삼성전자, SK하이닉스)."
                            " company 테이블의 name 컬럼으로 조회한다."
                        ),
                    },
                    "period_days": {
                        "type": "integer",
                        "description": (
                            "조회할 기간(일). 기본값 1. 2 이상이면 응답의 prices"
                            "리스트에 날짜별(등락률/거래량/종가) 데이터가 여러 건 담긴다."
                        ),
                    },
                },
                "required": ["ticker"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "news_tool",
            "description": (
                "DB에 저장된 실제 데이터 기준으로, 특정 종목과 관련된 최신 뉴스를"
                "조회한다. 뉴스가 필요한 질문에만 호출한다 (예: 최근 이슈, 왜 올랐는지"
                "/내렸는지, 관련 소식 등). 단순 주가·등락률·거래량 수치만 필요한"
                "질문에는 호출하지 않는다. 아직 뉴스가 수집되지 않은 종목이거나"
                "등록되지 않은 종목인 경우 found=false와 함께 그 사유를 담은"
                "message를 반환한다."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "company_name": {
                        "type": "string",
                        "description": (
                            "종목명 또는 종목코드(예: 삼성전자, 005930)."
                            " company 테이블의 name 또는 ticker 컬럼으로 조회한다."
                        ),
                    },
                    "limit": {
                        "type": "integer",
                        "description": "조회할 최근 뉴스 건수. 기본값 5.",
                    },
                },
                "required": ["company_name"],
            },
        },
    },
]

AVAILABLE_FUNCTIONS = {"stock_tool": stock_tool, "news_tool": news_tool}


def ask(question: str):
    print(f"\n{'='*60}\n질문: {question}\n{'='*60}")

    messages = [
        {
            "role": "system",
            "content": (
                "너는 한국 주식 투자자를 돕는 리서치 어시스턴트다. "
                "반드시 한국어로만 답변하고 다른 언어를 절대 섞지 마라. "
                "주가·등락률·거래량 등 수치 조회가 필요한 질문에는 stock_tool을 호출하고, "
                "최근 이슈나 '왜 올랐는지/내렸는지' 같이 뉴스가 필요한 질문에는 news_tool을 호출하라. "
                "두 종류의 정보가 모두 필요한 질문이면 stock_tool과 news_tool을 함께 호출하라. "
                "일반적인 용어 설명이나 개념 질문에는 절대 tool을 호출하지 말고 바로 답변하라. "
                "답변은 절대 JSON 형식으로 하지 말고, 자연스러운 문장으로 답하라."
            ),
        },
        {"role": "user", "content": question},
    ]

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
            try:
                result = AVAILABLE_FUNCTIONS[fn_name](**fn_args)
            except Exception as e:
                result = {"error": f"{fn_name} 실행 중 오류: {e}"}

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
    # 케이스 1: stock_tool + news_tool을 동시에 호출해야 하는 질문
    # (등락 수치는 stock_tool, "왜 올랐는지"는 news_tool 없이는 답할 근거가 없음)
    ask("삼성전자 오늘 왜 올랐어? 최근 주가랑 관련 뉴스 같이 확인해줘.")

    # 케이스 2: stock_tool만 필요한 질문
    ask("삼성전자 최근 3일 등락률이랑 거래량 알려줘.")

    # 케이스 3: news_tool만 필요한 질문
    ask("삼성전자 관련 최근 뉴스 좀 알려줘.")

    # 케이스 4: tool 호출 없이도 답할 수 있는 일반 질문
    # (모델이 불필요하게 tool을 남발하지 않는지 확인하는 목적)
    ask("주식 투자를 처음 시작할 때 알아야 할 기본 용어를 알려줘.")
