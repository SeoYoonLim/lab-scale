"""
tool-calling 신뢰도 벤치마크: llama3.1:8b vs qwen2.5:7b-instruct

app.agent의 동일한 SYSTEM_PROMPT/TOOLS/few-shot으로, 모델만 바꿔가며
아래 3가지 케이스를 각각 5회씩 반복 실행하고 성공률을 비교한다.

- multi_tool: "왜 올랐어" 질문 -> stock_tool과 news_tool을 함께 호출해야 성공
- no_tool: "기본 용어" 질문 -> 어떤 tool도 호출하지 않아야 성공
- no_language_leak: 주가 질문에 대한 답변이 한국어로만 구성돼야 성공
  (과거 "fix: connect real stock_tool to Ollama agent, fix language leak" 커밋에서
  다뤘던 회귀 여부 확인)

주의 (1차 실행에서 발견한 벤치마크 설계 함정, 재발 방지 메모):
  재현 조건: app.agent.FEW_SHOT_MESSAGES의 예시 질문 문구/종목명이 아래 CASE_* 문구와
             글자 그대로 겹칠 때.
  증상: 모델이 실제 tool을 호출하지 않고, few-shot 대화에 들어있던 assistant의
        가짜 tool 결과·가짜 숫자(예: change_pct, volume, 뉴스 제목)를 그대로 베껴서
        최종 답변으로 내놓음. 1차 실행에서 qwen2.5:7b-instruct의 multi_tool 케이스가
        5/5 모두 이 패턴으로 재현됨 (used_tools=[]인데 few-shot 속 가짜 수치가
        답변에 그대로 등장).
  왜 벤치마크 설계 결함인가: few-shot 메시지는 모델에게 "이 대화에서 이미 답변한
        내용"으로 인식되므로, 테스트 질문과 few-shot 질문이 같으면 모델의 실제
        tool-calling 판단 능력이 아니라 "이전 대화 재사용" 행동을 측정하게 되어
        점수가 왜곡된다. 그래서 few-shot 예시는 반드시 실제 테스트/운영 질문과
        종목명·문구가 겹치지 않도록 구성해야 한다 (현재는 SK하이닉스 기준으로 분리).

실행:
    python scripts/benchmark_models.py
"""

import json
import os
import re
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.agent import ask_question  # noqa: E402

MODELS = ["llama3.1:8b", "qwen2.5:7b-instruct"]
REPS = 5

CASE_MULTI = "삼성전자 오늘 왜 올랐어? 최근 주가랑 관련 뉴스 같이 확인해줘."
CASE_NONE = "주식 투자를 처음 시작할 때 알아야 할 기본 용어를 알려줘."
CASE_LANG = "삼성전자 최근 3일 등락률이랑 거래량 알려줘."

LATIN_WORD_RE = re.compile(r"[A-Za-z]{2,}")
# 금융 용어에서 자연스럽게 등장할 수 있는 허용 약어 (이 정도는 언어 누출로 보지 않음)
ALLOWED_LATIN = {"AI", "IT", "PBR", "PER", "ROE", "GDP", "KOSPI", "KOSDAQ"}


def has_language_leak(text: str) -> bool:
    words = LATIN_WORD_RE.findall(text)
    leaked = [w for w in words if w.upper() not in ALLOWED_LATIN]
    return len(leaked) > 0


def run_multi_tool(model: str):
    result = ask_question(CASE_MULTI, model=model)
    ok = "stock_tool" in result["used_tools"] and "news_tool" in result["used_tools"]
    return ok, result


def run_no_tool(model: str):
    result = ask_question(CASE_NONE, model=model)
    ok = len(result["used_tools"]) == 0
    return ok, result


def run_no_language_leak(model: str):
    result = ask_question(CASE_LANG, model=model)
    ok = not has_language_leak(result["answer"])
    return ok, result


CASES = {
    "multi_tool": run_multi_tool,
    "no_tool": run_no_tool,
    "no_language_leak": run_no_language_leak,
}


def main():
    raw = defaultdict(lambda: defaultdict(list))

    for model in MODELS:
        for case_name, runner in CASES.items():
            for rep in range(1, REPS + 1):
                try:
                    ok, result = runner(model)
                except Exception as e:
                    ok, result = False, {"error": str(e)}
                raw[model][case_name].append({"ok": ok, **result} if "error" not in result else {"ok": ok, **result})
                print(f"[{model}][{case_name}] rep {rep}/{REPS}: {'OK' if ok else 'FAIL'}")

    summary = {}
    for model in MODELS:
        summary[model] = {}
        for case_name in CASES:
            reps = raw[model][case_name]
            success = sum(1 for r in reps if r["ok"])
            summary[model][case_name] = f"{success}/{REPS}"

    out_dir = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(out_dir, "benchmark_raw.json"), "w", encoding="utf-8") as f:
        json.dump(raw, f, ensure_ascii=False, indent=2)
    with open(os.path.join(out_dir, "benchmark_summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    print("\n=== SUMMARY ===")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
