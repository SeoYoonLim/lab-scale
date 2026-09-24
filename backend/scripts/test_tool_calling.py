"""
Ollama tool calling 검증 스크립트

목적: qwen2.5:7b-instruct 모델이 실제로 "함수(tool)를 선택해서 호출"할 수 있는지
      최소 구조로 확인한다. (AI 투자 리서치 에이전트의 핵심 리스크 검증)

app.agent.ask_question()을 그대로 호출하는 CLI 래퍼. 실제 tool 스펙/오케스트레이션
로직은 app/agent.py에 있고, FastAPI의 POST /api/research도 같은 함수를 사용한다.

사전 준비:
1) Ollama 설치: https://ollama.com/download
2) 모델 받기:   ollama pull llama3.1:8b
3) 파이썬 패키지: pip install ollama

실행:
    python test_tool_calling.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.agent import ask_question  # noqa: E402


def ask(question: str):
    print(f"\n{'='*60}\n질문: {question}\n{'='*60}")

    result = ask_question(question, save_report=False)

    if result["used_tools"]:
        print(f"[Tool 호출 감지] {result['used_tools']}")
    else:
        print("[Tool 호출 없음]")

    print("최종 답변:", result["answer"])


if __name__ == "__main__":
    # 케이스 1: stock_tool + news_tool을 동시에 호출해야 하는 질문
    # (등락 수치는 stock_tool, "왜 올랐는지"는 news_tool 없이는 답할 근거가 없음)
    ask("삼성전자 오늘 왜 올랐어? 최근 주가랑 관련 뉴스 같이 확인해줘.")

    # 케이스 2: stock_tool만 필요한 질문
    ask("삼성전자 최근 3일 등락률이랑 거래량 알려줘.")

    # 케이스 3: news_tool만 필요한 질문
    ask("삼성전자 관련 최근 뉴스 좀 알려줘.")

    # 케이스 4: disclosure_tool만 필요한 질문
    ask("삼성전자 최근 공시 알려줘.")

    # 케이스 5: tool 호출 없이도 답할 수 있는 일반 질문
    # (모델이 불필요하게 tool을 남발하지 않는지 확인하는 목적)
    ask("주식 투자를 처음 시작할 때 알아야 할 기본 용어를 알려줘.")
