"""숫자 검증기(app/answer_guard.guard_answer) 평가: tests/fixtures/guard_cases.json(정상/위반 답변 쌍)으로 재현율·정밀도를 잰다.

검증기가 답변을 막았다 = guard_answer 기록의 replaced가 True(코드 요약으로 바꿈 또는 문장 삭제).
- 재현율(recall) = 막은 위반 / 전체 위반
- 정밀도(precision) = 막은 위반 / 막은 전체(위반 + 오탐 정상)
- 정상 통과율(normal_pass_rate) = 막지 않은 정상 / 전체 정상 (1 - 오탐률)
tests/test_guard_eval.py와 보고용 실행(python tests/guard_eval.py)이 같이 쓴다.
"""

import json
from collections import defaultdict
from datetime import date
from pathlib import Path

from app.answer_guard import guard_answer

CASES = Path(__file__).resolve().parent / "fixtures" / "guard_cases.json"
TODAY = date(2026, 10, 28)  # 세트를 만든 날(시나리오의 마지막 시세 다음 날). 실행 날짜에 따라 결과가 바뀌지 않게 고정한다


def _guard(answer, records, question):
    try:
        return guard_answer(answer, records, question, today=TODAY)
    except TypeError:  # today 인자가 없던 옛 검증기(개선 전 측정)
        return guard_answer(answer, records, question)


def load_cases(path: Path = CASES) -> tuple[dict, list[dict]]:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return data["scenarios"], data["cases"]


def run(guard=_guard, path: Path = CASES) -> dict:
    scenarios, cases = load_cases(path)
    rows = []
    for c in cases:
        _, log = guard(c["answer"], scenarios[c["scenario"]], c["question"])
        rows.append({**c, "blocked": bool(log["replaced"]), "violations": log.get("violations")})
    viol = [r for r in rows if r["label"] == "violation"]
    ok = [r for r in rows if r["label"] == "ok"]
    caught = sum(r["blocked"] for r in viol)
    false_alarm = sum(r["blocked"] for r in ok)
    by_type = defaultdict(lambda: [0, 0])
    for r in viol:
        by_type[r["type"]][0] += r["blocked"]
        by_type[r["type"]][1] += 1
    return {
        "n_violation": len(viol),
        "n_ok": len(ok),
        "recall": caught / len(viol) if viol else None,
        "precision": caught / (caught + false_alarm) if caught + false_alarm else None,
        "normal_pass_rate": (len(ok) - false_alarm) / len(ok) if ok else None,
        "recall_by_type": {t: {"caught": a, "total": b, "recall": a / b} for t, (a, b) in sorted(by_type.items())},
        "missed": [r["id"] for r in viol if not r["blocked"]],
        "false_alarms": [r["id"] for r in ok if r["blocked"]],
        "rows": rows,
    }


if __name__ == "__main__":
    import sys

    sys.stdout.reconfigure(encoding="utf-8")
    res = run()
    print(json.dumps({k: v for k, v in res.items() if k != "rows"}, ensure_ascii=False, indent=2))
    for r in res["rows"]:
        if (r["label"] == "violation") != r["blocked"]:
            print(r["id"], r["label"], r.get("type"), "blocked" if r["blocked"] else "passed", "|", r["answer"])
