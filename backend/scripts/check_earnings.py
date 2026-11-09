"""분기 실적(quarterly_earnings) 점검(2026-11-04).

- 기본: DB만 읽는다. 전 종목 행에 app.earnings.anomalies(|당기순이익| > 매출액, 분기 값 > 누적, 1~n분기 합 != 누적,
  보고서 전기 금액 != 전년 행, 전년 동기 대비 ±500% 초과)를 돌려 목록과 종류별 개수를 출력한다.
- --compare 종목명...: DART 원본(fnlttSinglAcnt)을 다시 받아 최근 4분기(답변과 같은 분기 계산)의 원본 값(thstrm_amount·
  thstrm_add_amount)과 DB·답변 값을 나란히 출력한다. 외부 요청이므로 실적 수집과 같은 시간 제한(평일 장중 금지)을 지킨다.

사용:
  python -m scripts.check_earnings                         # 이상치 목록(DB 읽기만)
  python -m scripts.check_earnings --json OUT.json         # 목록을 파일로
  python -m scripts.check_earnings --compare SK하이닉스 삼성전자 NAVER   # DART 원본 대조(외부 요청)
"""

import argparse
import json
import sys
import time
from collections import Counter

from app import earnings


def _kind(line: str) -> str:
    for key in ("|당기순이익|", "매출 계정 인식 의심", "분기 값", "합", "전기 금액", "전년 동기 대비", "1분기 값"):
        if key in line:
            return key
    return "기타"


def scan(db) -> dict:
    from app.models import Company, QuarterlyEarnings

    rows = db.query(QuarterlyEarnings, Company.name).join(Company, Company.id == QuarterlyEarnings.company_id).all()
    by: dict[str, list] = {}
    for r, name in rows:
        by.setdefault(name, []).append(r)
    found = {name: earnings.anomalies(rs) for name, rs in sorted(by.items())}
    found = {k: v for k, v in found.items() if v}
    no_revenue = sorted(name for name, rs in by.items()
                        if any(r.revenue is None for r in rs if r.bsns_year == max(x.bsns_year for x in rs)))
    kinds = Counter(_kind(line) for v in found.values() for line in v)
    return {"companies": len(by), "rows": len(rows), "with_anomaly": len(found), "kinds": dict(kinds), "anomalies": found,
            "revenue_missing_latest_year": no_revenue}


def compare(db, names: list[str], log=print) -> list[dict]:
    """DART 원본과 DB·답변(recent_for_answer 4분기) 대조. 요청 수를 함께 돌려준다."""
    from app.collectors import get_dart_corp_code
    from app.http_retry import get as http_get
    from app.models import Company, QuarterlyEarnings
    import os

    out = []
    for name in names:
        c = db.query(Company).filter(Company.name == name).first()
        corp = get_dart_corp_code(c.ticker) if c else None
        if not corp:
            log(f"{name}: corp_code 없음")
            continue
        rows = db.query(QuarterlyEarnings).filter(QuarterlyEarnings.company_id == c.id).all()
        series = earnings.quarterly_series(rows)
        for year, q in sorted(series, reverse=True)[:4]:
            v = series[(year, q)]
            codes = ["11011", "11014"] if q == 4 else [{1: "11013", 2: "11012", 3: "11014"}[q]]
            raw = {}
            for code in codes:
                resp = http_get(earnings.DART_ACCOUNT_URL, params={"crtfc_key": os.environ["DART_API_KEY"], "corp_code": corp,
                                                                   "bsns_year": str(year), "reprt_code": code}, label="DART check")
                time.sleep(earnings.REQUEST_INTERVAL_S)
                items = [x for x in resp.json().get("list") or [] if x.get("fs_div") == v["fs_div"] and x.get("sj_div") in ("IS", "CIS")]
                for key, match in (("revenue", lambda n: n in earnings.REVENUE_NAMES),
                                   ("operating_income", lambda n: n.startswith(earnings.OPERATING_PREFIX)),
                                   ("net_income", lambda n: n.startswith(earnings.NET_PREFIX))):
                    x = next((x for x in items if match(x.get("account_nm") or "")), None)
                    raw[(code, key)] = (earnings._amount(x.get("thstrm_amount")) if x else None,
                                        earnings._amount(x.get("thstrm_add_amount")) if x else None,
                                        x.get("account_nm") if x else None)
            for key in earnings.ACCOUNTS:
                if q == 4:
                    annual, q3cum = raw[("11011", key)][0], raw[("11014", key)][1]
                    dart = annual - q3cum if annual is not None and q3cum is not None else None
                    src = f"연간 {annual} - 3분기 누적 {q3cum}"
                else:
                    dart, cum, acct = raw[(codes[0], key)]
                    src = f"thstrm {dart}, 누적 {cum}, 계정 {acct}"
                out.append({"company": name, "period": f"{year}년 {q}분기", "fs_div": v["fs_div"], "account": key,
                            "db_or_answer": v.get(key), "dart": dart, "match": v.get(key) == dart, "dart_source": src})
    return out


def main(argv=None) -> int:
    from dotenv import load_dotenv

    from app.db.session import SessionLocal

    load_dotenv()
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", help="결과를 이 파일로 저장")
    ap.add_argument("--compare", nargs="+", help="DART 원본과 대조할 종목명(외부 요청)")
    a = ap.parse_args(argv)
    db = SessionLocal()
    try:
        result = scan(db)
        if a.compare:
            result["compare"] = compare(db, a.compare)
    finally:
        db.close()
    print(f"종목 {result['companies']}개, 행 {result['rows']}개, 이상치 있는 종목 {result['with_anomaly']}개, 종류 {result['kinds']}")
    for name, lines in result["anomalies"].items():
        for line in lines:
            print(f"  {name}: {line}")
    for r in result.get("compare", []):
        print(f"  [대조] {r['company']} {r['period']} {r['account']}: 답변 {r['db_or_answer']} / DART {r['dart']} "
              f"{'일치' if r['match'] else '불일치'} ({r['dart_source']})")
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=1, default=str)
    return 0


if __name__ == "__main__":
    sys.exit(main())
