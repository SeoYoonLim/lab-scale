"""CI용 평가 게이트와 요약 표. run_eval이 만든 summary JSON을 읽는다(평가기 metrics.py의 판정은 건드리지 않는다).

    python -m evals.ci_gate evals/results/summary_ci_offline.json            # 게이트 판정 + 마크다운 표를 stdout으로
    python -m evals.ci_gate summary.json >> "$GITHUB_STEP_SUMMARY"          # GitHub Actions job summary에 붙이기

게이트(하나라도 어기면 종료 코드 1): 종목 오매칭률 entity_default_wrong_rate, entity_strict_wrong_rate가 0이어야 한다.
오매칭은 다른 종목으로 잘못 연결하는 것이라 못 찾는 것보다 나쁘다(주문 API는 이 오매칭이 곧 체결 사고다). 나머지 지표는 게이트가 아니라 표로만 보여준다.
live 모드(Ollama 필요)는 CI에서 돌리지 않는다.
"""

import argparse
import json
import sys

GATE_METRICS = ("entity_default_wrong_rate", "entity_strict_wrong_rate")
SPLITS = ("dev", "holdout", "all")


def evaluate_gate(metrics: dict) -> list[str]:
    """게이트 위반 목록(비면 통과). 지표가 없거나 표본이 0이면 통과가 아니라 위반이다(측정을 못 한 채로 통과하면 안 된다)."""
    problems = []
    for name in GATE_METRICS:
        m = metrics.get(name)
        if not m or not m.get("n", {}).get("all"):
            problems.append(f"{name}: 측정되지 않음(표본 0)")
            continue
        for split in SPLITS:
            v = m.get(split)
            if v is not None and v != 0:
                problems.append(f"{name}[{split}] = {v} (0이어야 함)")
    return problems


def _fmt(v) -> str:
    return "-" if v is None else f"{v:g}"


def markdown(summary: dict) -> str:
    metrics = summary["metrics"]
    problems = evaluate_gate(metrics)
    lines = [
        "## AI 평가 (offline)",
        "",
        f"게이트: **{'통과' if not problems else '실패'}** - 종목 오매칭률 "
        + ", ".join(f"`{n}`" for n in GATE_METRICS) + " = 0",
    ]
    lines += [f"- ❌ {p}" for p in problems]
    lines += ["", "| 지표 | dev | holdout | 전체 | n |", "|---|---|---|---|---|"]
    for name, m in metrics.items():
        mark = " 🚧" if name in GATE_METRICS else ""
        lines.append(f"| {name}{mark} | {_fmt(m.get('dev'))} | {_fmt(m.get('holdout'))} | {_fmt(m.get('all'))} | {m['n']['all']} |")
    failures = summary.get("failures", [])
    lines += ["", f"실패 문항 {len(failures)}건 (엄격 모드가 음차·약칭·오타를 거절하는 것처럼 설계된 동작 포함, 게이트 아님):"]
    for f in failures[:15]:
        lines.append(f"- `{f['id']}` ({f['split']}) {f['question']} - {f['check']}")
    if len(failures) > 15:
        lines.append(f"- ... 외 {len(failures) - 15}건")
    lines += ["", "🚧 = 게이트 지표. live 평가(Ollama 필요)는 CI에서 돌지 않는다: `backend/evals/README.md` 참고."]
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="평가 summary의 CI 게이트")
    ap.add_argument("summary", help="run_eval이 만든 summary JSON")
    args = ap.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    with open(args.summary, encoding="utf-8") as f:
        summary = json.load(f)
    print(markdown(summary))
    problems = evaluate_gate(summary["metrics"])
    if problems:
        print("\n게이트 실패:\n" + "\n".join(f"  {p}" for p in problems), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
