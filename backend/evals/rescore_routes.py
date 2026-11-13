"""다국어 실답 결과(evals/run_multilingual.py의 jsonl)를 다른 기대값 세트로 다시 채점한다(2026-11-13, 50차).

답을 다시 만들지 않는다. 결과 줄의 observed(실제 경로·기간·종목)를 그대로 쓰고, 기대값만 cases 파일에서 다시 읽어
route_checks.route_ok / period_wrong으로 판정한다. 그래서 같은 실행을 v1·v2 기대값으로 나눠 비교할 수 있다.

  python -m evals.rescore_routes <결과.jsonl> evals/multilingual_cases_49.txt evals/multilingual_cases_49_v2.txt [--run-date 2026-11-13]

--run-date: 그 실행의 날짜. "1개월"(달력 한 달 평일 수, app/period_parser)이 그날 몇 거래일인지 보여 주고, v2의 days=24와
다르면 경고한다(24는 2026-11-13 기준 값).
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import date, datetime

try:
    from evals import route_checks
except ImportError:  # pragma: no cover
    import route_checks  # type: ignore

V2_MONTH_DAYS = 24


def read_cases(path: str) -> dict[str, dict]:
    out = {}
    for line in open(path, encoding="utf-8"):
        line = line.rstrip("\n")
        if not line or line.startswith("#"):
            continue
        sp, cid, loc, exp, spec, q = line.split("|", 5)
        out[cid] = {"locale": loc, "expected": [] if exp == "-" else exp.split(","), "spec": spec, "question": q}
    return out


def score(results: list[dict], cases: dict[str, dict]) -> dict:
    by_loc_ok, by_loc_n = Counter(), Counter()
    period_wrong, fails = 0, []
    for r in results:
        c = cases.get(r["id"])
        if c is None:
            continue
        expect = route_checks.parse_expect(c["spec"])
        ok = route_checks.route_ok(expect, r.get("observed") or {}, c["expected"])
        lang = c["locale"].split("-")[0]
        if ok is not None:
            by_loc_n[lang] += 1
            by_loc_ok[lang] += bool(ok)
            if not ok:
                fails.append({"id": r["id"], "expect": c["spec"], "observed": r.get("observed")})
        period_wrong += route_checks.period_wrong(expect, r.get("observed") or {})
    return {"intent_ok": sum(by_loc_ok.values()), "judged": sum(by_loc_n.values()),
            "by_locale": {k: f"{by_loc_ok[k]}/{by_loc_n[k]}" for k in sorted(by_loc_n)},
            "period_wrong": period_wrong, "fails": fails}


def month_days(run_date: date) -> int | None:
    from app import period_parser

    p = period_parser.parse_period("최근 1개월", now=datetime.combine(run_date, datetime.min.time()).replace(hour=12))
    return getattr(p, "period_days", None) if p else None


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("results")
    ap.add_argument("cases", nargs="+")
    ap.add_argument("--run-date", type=date.fromisoformat)
    args = ap.parse_args(argv)
    results = [json.loads(x) for x in open(args.results, encoding="utf-8") if x.strip()]
    out = {"results": args.results, "rows": len(results)}
    if args.run_date:
        m = month_days(args.run_date)
        out["month_trading_days_on_run_date"] = m
        if m != V2_MONTH_DAYS:
            out["warning"] = f"실행일 {args.run_date}의 1개월 = {m}거래일(v2 기대값 {V2_MONTH_DAYS}과 다름)"
    out["by_cases"] = {path: score(results, read_cases(path)) for path in args.cases}
    print(json.dumps(out, ensure_ascii=False, indent=1, default=str))


if __name__ == "__main__":
    main()
