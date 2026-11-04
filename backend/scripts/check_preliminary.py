"""잠정실적(거래소 공정공시) 점검(2026-11-04). DB 읽기만, 외부 요청 없음.

    python -m scripts.check_preliminary            # 요약
    python -m scripts.check_preliminary --json OUT # 전체 결과 JSON

- 파싱: 제목에 "(잠정)실적"이 있는 공시를 app.preliminary로 읽고, 쓰지 않은 이유(자회사 실적·정정공시·분기 아님·표 없음)를 센다.
- 선택: 답변과 같은 규칙(app.earnings.recent_for_answer)으로 정기보고서와 같은 기준(연결·별도)의 잠정치만 고르고, 2~3분기는
  누적 일관성(직전 분기 정기보고서 누적 + 이번 분기 당해실적 = 잠정 누계)이 맞는 것만 쓴다. 기준이 다르거나 미표기, 누적이 안 맞아
  빠진 수를 따로 센다.
- 잠정 vs 확정: 고른 잠정치와 같은 분기의 정기보고서(4분기는 연간 - 3분기 누적)가 있으면 매출액·영업이익을 비교한다.
  공시 단위(조원·억원·백만원)의 반올림이 있으므로 차이 0.5%(또는 단위 1개) 안이면 일치로 본다. 기준을 가리지 않고 고른 경우
  (기존 규칙)의 불일치도 함께 센다.
- 최신 분기가 잠정치인 종목 수(답변에 잠정치가 나오는 종목).
"""

import argparse
import json
import sys
from collections import Counter

from dotenv import load_dotenv

from app import earnings, preliminary

TOLERANCE = 0.005
KEYS = ("revenue", "operating_income")


def _close(a: float | None, b: float | None, unit: float) -> bool | None:
    if a is None or b is None:
        return None
    return abs(a - b) <= max(abs(b) * TOLERANCE, unit)


def _skip_reason(title: str, content: str) -> str:
    if "자회사" in title:
        return "자회사 실적"
    if preliminary._CORRECTION_RE.match(title):
        return "정정공시"
    p = preliminary._PERIOD_RE.search(content or "")
    if not p:
        return "실적기간 없음"
    y1, m1, d1, y2, m2, _ = map(int, p.groups())
    if d1 != 1 or m1 not in (1, 4, 7, 10) or y2 != y1 or m2 != m1 + 2:
        return "분기 실적 아님(월별·판매 등)"
    if not preliminary._UNIT_RE.search(content or ""):
        return "단위 없음"
    return "매출액·영업이익 없음"


def scan(db) -> dict:
    from app.models import Company, Disclosure, QuarterlyEarnings

    rows = (db.query(Disclosure.company_id, Disclosure.title, Disclosure.content, Disclosure.source_url)
            .filter(Disclosure.title.like("%(잠정)실적%")).order_by(Disclosure.disclosed_at, Disclosure.id).all())
    names = dict(db.query(Company.id, Company.name).all())
    reasons, parsed = Counter(), 0
    by_company: dict[int, list] = {}
    for cid, title, content, url in rows:
        by_company.setdefault(cid, []).append((title, content, url))
        if preliminary.parse(title, content) is not None:
            parsed += 1
        else:
            reasons[_skip_reason(title or "", content or "")] += 1
    compared, mismatches, naive_mismatches, latest_prelim, corrected = 0, [], 0, [], []
    dropped = Counter()
    for cid, items in by_company.items():
        qrows = db.query(QuarterlyEarnings).filter(QuarterlyEarnings.company_id == cid).all()
        finals = earnings.quarterly_series(qrows)
        basis = "별도" if finals and finals[max(finals)]["fs_div"] == "OFS" else "연결"
        cums = {(r.bsns_year, earnings.QUARTER_OF[r.reprt_code]): r.revenue_cum for r in qrows if r.reprt_code in earnings.QUARTER_OF}
        naive = preliminary.from_rows(items)
        chosen = preliminary.from_rows(items, basis)
        for key, p in naive.items():
            f = finals.get(key)
            if f is not None and p["corrected"] is None and any(_close(p[k], f.get(k), preliminary.UNITS[p["unit"]]) is False for k in KEYS):
                naive_mismatches += 1
            if key not in chosen:
                dropped["기준 미표기" if p["basis"] == "기준 미표기" else "기준이 정기보고서와 다름"] += 1
        used = {}
        for (y, q), p in chosen.items():
            if q > 1 and not preliminary.cumulative_ok(p, cums.get((y, q - 1))):
                dropped["누적 불일치"] += 1
                continue
            used[(y, q)] = p
            unit = preliminary.UNITS[p["unit"]]
            if p["corrected"]:
                corrected.append(f"{names[cid]} {y}년 {q}분기 정정 {p['corrected']['rcept_no']}")
            f = finals.get((y, q))
            if f is not None and p["corrected"] is None:
                compared += 1
                for k in KEYS:
                    if _close(p[k], f.get(k), unit) is False:
                        mismatches.append(f"{names[cid]} {y}년 {q}분기 {k}: 잠정 {p[k] / 1e8:,.0f}억 / 확정 {f[k] / 1e8:,.0f}억 "
                                          f"({p['basis']} / {f['fs_div']})")
        keys = set(finals) | {k for k, p in used.items() if p["revenue"] is not None and p["operating_income"] is not None}
        if keys:
            k = max(keys)
            if k not in finals:
                latest_prelim.append(f"{names[cid]} {k[0]}년 {k[1]}분기")
    return {"disclosures": len(rows), "parsed": parsed, "skipped": dict(reasons), "companies": len(by_company),
            "compared_quarters": compared, "mismatches": mismatches, "naive_mismatch_quarters": naive_mismatches,
            "dropped": dict(dropped),
            "latest_is_preliminary": sorted(latest_prelim), "corrected": corrected}


def main(argv=None) -> int:
    load_dotenv()
    ap = argparse.ArgumentParser(description="잠정실적 공정공시 파싱·정합성 점검(DB 읽기만)")
    ap.add_argument("--json", help="전체 결과 JSON 경로")
    args = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        r = scan(db)
    finally:
        db.close()
    print(f"잠정실적 공시 {r['disclosures']}건(종목 {r['companies']}개), 분기 표 파싱 {r['parsed']}건, 제외 {r['skipped']}")
    print(f"기준을 가리지 않으면 잠정·확정 불일치 분기 {r['naive_mismatch_quarters']}개. 답변 규칙으로 뺀 잠정치 {r['dropped']}")
    print(f"답변 규칙으로 고른 잠정치 vs 확정 비교 {r['compared_quarters']}개 분기, 불일치 {len(r['mismatches'])}건")
    for x in r["mismatches"]:
        print(f"  {x}")
    print(f"최신 분기가 잠정치인 종목 {len(r['latest_is_preliminary'])}개, 정정공시가 뒤따른 잠정치 {len(r['corrected'])}건")
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(r, f, ensure_ascii=False, indent=1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
