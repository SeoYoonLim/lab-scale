"""두 평가 summary의 지표별 변화를 마크다운 표로 출력한다.

    python -m evals.compare evals/results/summary_baseline_2026-10-27.json evals/results/summary_new.json
"""

import argparse
import json
import sys

from evals.run_eval import HIGHER_IS_BETTER, SPLITS


def _fmt(v):
    return "-" if v is None else f"{v:g}" if isinstance(v, (int, float)) else str(v)


def compare_table(old: dict, new: dict) -> str:
    """지표 x split 표. 개선은 ✅, 악화는 ⚠️ (지표마다 높을수록 좋은지/낮을수록 좋은지를 따른다)."""
    lines = [
        f"A: {(old.get('git_commit') or '?')[:8]} {old.get('date', '')} / B: {(new.get('git_commit') or '?')[:8]} {new.get('date', '')}",
        "",
        "| metric | split | A | B | Δ |",
        "|---|---|---|---|---|",
    ]
    names = list(old.get("metrics", {})) + [n for n in new.get("metrics", {}) if n not in old.get("metrics", {})]
    for name in names:
        a_row, b_row = old["metrics"].get(name, {}), new["metrics"].get(name, {})
        for split in SPLITS:
            a, b = a_row.get(split), b_row.get(split)
            if a is None or b is None:
                delta = "-"
            else:
                d = round(b - a, 4)
                mark = "" if d == 0 else ("✅" if (d > 0) == HIGHER_IS_BETTER.get(name, True) else "⚠️")
                delta = f"{d:+g} {mark}".strip()
            lines.append(f"| {name} | {split} | {_fmt(a)} | {_fmt(b)} | {delta} |")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="평가 summary 두 개 비교")
    ap.add_argument("old")
    ap.add_argument("new")
    args = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    with open(args.old, encoding="utf-8") as f:
        old = json.load(f)
    with open(args.new, encoding="utf-8") as f:
        new = json.load(f)
    print(compare_table(old, new))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
