"""(A, 2026-10-28) 최종 답변 반복 루프 완화 실험. 변수 하나씩: 대조군 / num_ctx 확대 / repeat_penalty / 도구 결과 축소.

루프 = 최종 답변이 num_predict(app.agent.LLM_NUM_PREDICT)에서 잘림(meta.llm_truncated). 질문마다 변수를 번갈아(같은 시간대에
섞이도록) runs회 돌리고, 루프율·평균 응답 시간·최종 호출의 프롬프트/생성 토큰·모델 로딩 시간·GPU 메모리(nvidia-smi)를 남긴다.
리포트는 저장하지 않는다(save_report=False). 실행: python -m scripts.loop_experiment --runs 15 --out 결과.json
"""

import argparse
import json
import statistics
import subprocess
import sys
import time

import ollama

import app.agent as agent

QUESTIONS = [
    "KODEX 레버리지는 어떤 상품이야? 주의할 점도 자세히 알려줘",  # 평가 dev stab_01(기준선 r5에서 루프 1회)
    "TIGER 200은 어떤 상품이야? 위험한 점도 알려줘",  # 2차 라운드 재현에서 3회 중 2회 루프(데이터셋 밖)
    "KODEX 코스닥150레버리지는 어떤 상품이고 주의할 점은 뭐야?",  # 데이터셋 밖
    "TIGER 미국S&P500은 어떤 상품이야? 위험 요인도 자세히 알려줘",  # 데이터셋 밖
]
VARIANTS = {
    "control": {},
    "ctx8192": {"options": {"num_ctx": 8192}},
    "repeat": {"options": {"repeat_penalty": 1.2, "repeat_last_n": 256}},
    "trim": {"trim": True},
}


def _trim(obj):
    """LLM에 넘기는 도구 결과만 줄인다: 뉴스 본문(content) 제거, 요약 문장이 있으면 일별 시세 목록 제거."""
    if isinstance(obj, dict):
        out = {k: _trim(v) for k, v in obj.items() if k != "content"}
        if out.get("summary") and "prices" in out:
            out.pop("prices")
        return out
    if isinstance(obj, list):
        return [_trim(v) for v in obj]
    return obj


class _TrimJson:
    """agent 모듈 안의 json.dumps만 바꾼다(도구 결과를 메시지로 넣는 곳). 기록(tool_call_log)은 바뀌지 않는다."""

    def __init__(self, real):
        self.real = real

    def __getattr__(self, name):
        return getattr(self.real, name)

    def dumps(self, obj, *a, **kw):
        return self.real.dumps(_trim(obj), *a, **kw)


def gpu_used_mib() -> int | None:
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=10).stdout
        return int(out.split()[0])
    except Exception:  # noqa: BLE001
        return None


def run_once(question: str, variant: str) -> dict:
    cfg = VARIANTS[variant]
    calls = []
    real_chat = ollama.Client.chat

    def capture(self, *a, **kw):
        resp = real_chat(self, *a, **kw)
        calls.append({"tools": bool(kw.get("tools")), "prompt_eval_count": resp.get("prompt_eval_count"),
                      "eval_count": resp.get("eval_count"), "load_s": (resp.get("load_duration") or 0) / 1e9,
                      "done_reason": resp.get("done_reason")})
        return resp

    orig_llm, orig_json = agent._llm_chat, agent.json

    def with_options(**kwargs):
        kwargs["options"] = {**(kwargs.get("options") or {}), **cfg.get("options", {})}
        return orig_llm(**kwargs)

    ollama.Client.chat = capture
    agent._llm_chat = with_options
    if cfg.get("trim"):
        agent.json = _TrimJson(orig_json)
    t0 = time.perf_counter()
    error = None
    try:
        resp = agent.ask_question(question, save_report=False)
    except Exception as e:  # noqa: BLE001
        resp, error = {}, f"{type(e).__name__}: {e}"
    finally:
        ollama.Client.chat = real_chat
        agent._llm_chat = orig_llm
        agent.json = orig_json
    meta = resp.get("meta") or {}
    final = [c for c in calls if not c["tools"]]
    return {
        "variant": variant, "question": question, "latency_s": round(time.perf_counter() - t0, 2), "error": error,
        "loop": bool(meta.get("llm_truncated")), "fallback": meta.get("fallback"),
        "guard_replaced": bool((meta.get("answer_guard") or {}).get("replaced")),
        "final_prompt_tokens": final[-1]["prompt_eval_count"] if final else None,
        "final_eval_tokens": final[-1]["eval_count"] if final else None,
        "load_s": round(sum(c["load_s"] for c in calls), 2), "gpu_mib": gpu_used_mib(),
        "answer_chars": len(resp.get("answer") or ""),
    }


def summarize(rows: list[dict]) -> dict:
    out = {}
    for v in VARIANTS:
        rs = [r for r in rows if r["variant"] == v]
        if not rs:
            continue
        prompts = [r["final_prompt_tokens"] for r in rs if r["final_prompt_tokens"]]
        out[v] = {
            "n": len(rs), "loop_rate": sum(r["loop"] for r in rs) / len(rs), "loops": sum(r["loop"] for r in rs),
            "errors": sum(bool(r["error"]) for r in rs), "fallback": sum(bool(r["fallback"]) for r in rs),
            "guard_replaced": sum(r["guard_replaced"] for r in rs),
            "latency_mean_s": round(statistics.mean(r["latency_s"] for r in rs), 2),
            "latency_max_s": max(r["latency_s"] for r in rs),
            "final_prompt_tokens_mean": round(statistics.mean(prompts)) if prompts else None,
            "final_prompt_tokens_max": max(prompts) if prompts else None,
            "load_s_total": round(sum(r["load_s"] for r in rs), 1),
            "gpu_mib_max": max((r["gpu_mib"] or 0) for r in rs),
            "by_question": {q[:20]: sum(r["loop"] for r in rs if r["question"] == q) for q in QUESTIONS if any(r["question"] == q for r in rs)},
        }
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=15)
    ap.add_argument("--variants", default=",".join(VARIANTS))
    ap.add_argument("--questions", type=int, default=len(QUESTIONS), help="앞에서 N개 질문만")
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")
    variants = args.variants.split(",")
    questions = QUESTIONS[: args.questions]
    rows = []
    # 변수별로 묶어 돈다: num_ctx가 바뀌면 Ollama가 모델을 다시 올려서, 번갈아 돌리면 매 호출 재로딩이 생긴다(예비 실행에서 확인)
    order = [(v, run, q) for v in variants for run in range(args.runs) for q in questions]
    for v, run, q in order:
        r = run_once(q, v)
        rows.append({**r, "run": run})
        print(f"[{run}] {v:8s} {r['latency_s']:6.1f}s loop={r['loop']} prompt={r['final_prompt_tokens']} "
              f"gen={r['final_eval_tokens']} load={r['load_s']} gpu={r['gpu_mib']} err={r['error']}", flush=True)
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump({"rows": rows, "summary": summarize(rows)}, f, ensure_ascii=False, indent=2)
    print(json.dumps(summarize(rows), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
