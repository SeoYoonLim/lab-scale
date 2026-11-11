"""저장 종가가 공식 종가(KRX 정규장 종가)와 같은지 읽기 전용으로 감사한다(2026-11-11, 44차). DB에 쓰지 않는다.

  python -m scripts.audit_close_basis --tickers 005930,000660,069500,102110,360750 --since 2025-09-01 \
      --dbs ai_investment,ai_investment_verify --dates 2026-09-11,2026-09-14,2026-11-02,2026-11-10,2026-11-11 --out <폴더>

DATABASE_URL의 DB 이름만 바꿔 각 DB를 READ ONLY 트랜잭션으로 읽는다. 공식 종가 복원 규칙은 app/official_close.py.
"""
import argparse
import json
import os
import time
from collections import Counter
from datetime import date
from pathlib import Path

import FinanceDataReader as fdr
from sqlalchemy import create_engine, text

from app import http_retry, official_close as oc

INDEX_URL = "https://m.stock.naver.com/api/index/{code}/price"
INDEXES = {"KS11": "KOSPI", "KQ11": "KOSDAQ"}


def db_rows(url: str, ticker: str, since: date) -> dict:
    eng = create_engine(url, connect_args={"connect_timeout": 10})
    with eng.connect() as c:
        c.execute(text("SET TRANSACTION READ ONLY"))
        rows = c.execute(text("""SELECT s.price_date, s.close_price, s.change_pct FROM stock_price s JOIN company c ON c.id=s.company_id
                                 WHERE c.ticker=:t AND s.price_date >= :d ORDER BY 1"""), {"t": ticker, "d": since}).all()
        at = c.execute(text("SELECT asset_type FROM company WHERE ticker=:t"), {"t": ticker}).scalar()
    eng.dispose()
    return {"asset_type": at, "rows": {r[0]: (float(r[1]), None if r[2] is None else float(r[2])) for r in rows}}


def db_index(url: str, code: str, since: date) -> dict:
    eng = create_engine(url, connect_args={"connect_timeout": 10})
    with eng.connect() as c:
        c.execute(text("SET TRANSACTION READ ONLY"))
        rows = c.execute(text("SELECT price_date, close_price FROM market_index WHERE index_code=:c AND price_date >= :d"),
                         {"c": code, "d": since}).all()
    eng.dispose()
    return {r[0]: float(r[1]) for r in rows}


def naver_index(code: str, pages: int = 3) -> dict:
    out = {}
    for p in range(1, pages + 1):
        batch = http_retry.get(INDEX_URL.format(code=code), params={"pageSize": oc.PAGE_SIZE, "page": p}, headers=oc.HEADERS,
                               timeout=oc.REQUEST_TIMEOUT_S, label="naver index daily").json()
        for r in batch:
            out[date.fromisoformat(r["localTradedAt"][:10])] = float(str(r["closePrice"]).replace(",", ""))
        time.sleep(0.5)
        if len(batch) < oc.PAGE_SIZE:
            break
    return out


def main():
    ap = argparse.ArgumentParser(description="저장 종가 vs 공식 종가(복원) 읽기 전용 감사")
    ap.add_argument("--tickers", default="005930,000660,069500,102110,360750")
    ap.add_argument("--since", default="2025-09-01")
    ap.add_argument("--dbs", default="ai_investment")
    ap.add_argument("--dates", default="")
    ap.add_argument("--out", required=True)
    ap.add_argument("--delay", type=float, default=0.6)
    a = ap.parse_args()
    since = date.fromisoformat(a.since)
    focus = [date.fromisoformat(x) for x in a.dates.split(",") if x]
    base_url = os.environ["DATABASE_URL"].rsplit("/", 1)[0]
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    report = {"since": a.since, "tickers": {}, "indexes": {}}
    for t in a.tickers.split(","):
        dbs = {name: db_rows(f"{base_url}/{name}", t, since) for name in a.dbs.split(",")}
        asset_type = next((v["asset_type"] for v in dbs.values() if v["asset_type"]), "stock")
        days = oc.classify(oc.fetch_daily(t, since), asset_type)
        time.sleep(a.delay)
        fdr_close = {i.date(): float(r["Close"]) for i, r in fdr.DataReader(t, a.since).iterrows()}
        kinds = Counter(d.kind for d in days)
        per_db = {}
        for name, v in dbs.items():
            stored = v["rows"]
            diff = [(d.day.isoformat(), stored[d.day][0], d.official_close, d.kind) for d in days
                    if d.official_close is not None and d.day in stored and abs(stored[d.day][0] - d.official_close) >= 0.5]
            per_db[name] = {"rows": len(stored), "latest": max(stored).isoformat() if stored else None,
                            "differs_from_official": len(diff), "first": diff[:3], "last": diff[-3:],
                            "by_kind": dict(Counter(x[3] for x in diff))}
        table = []
        for fd in focus:
            d = next((x for x in days if x.day == fd), None)
            table.append({"date": fd.isoformat(), "naver_chart_close": d.source_close if d else None,
                          "official_close": d.official_close if d else None, "kind": d.kind if d else "no trading/row",
                          "fdr_close": fdr_close.get(fd), **{f"db_{n}": (v["rows"].get(fd) or (None,))[0] for n, v in dbs.items()}})
        report["tickers"][t] = {"asset_type": asset_type, "naver_days": len(days), "kinds": dict(kinds),
                                "after_market_days": [d.day.isoformat() for d in days if d.kind == oc.AFTER_MARKET],
                                "other_adjusted_days": [(d.day.isoformat(), d.kind) for d in days if d.kind in (oc.DISTRIBUTION, oc.BASE_ADJUSTED)],
                                "fdr_equals_naver_chart": sum(1 for d in days if d.day in fdr_close and abs(fdr_close[d.day] - d.source_close) < 0.5),
                                "fdr_overlap": sum(1 for d in days if d.day in fdr_close),
                                "per_db": per_db, "focus": table}
        print(t, asset_type, dict(kinds), {n: (p["differs_from_official"], p["by_kind"]) for n, p in per_db.items()})
    for code, ncode in INDEXES.items():
        nv = naver_index(ncode)
        fv = {i.date(): float(r["Close"]) for i, r in fdr.DataReader(code, a.since).iterrows()}
        dbv = {n: db_index(f"{base_url}/{n}", code, since) for n in a.dbs.split(",")}
        diff_fdr = sorted((d.isoformat(), fv[d], nv[d]) for d in nv if d in fv and abs(fv[d] - nv[d]) >= 0.005)
        report["indexes"][code] = {"naver_days": len(nv), "fdr_vs_naver_diff": diff_fdr,
                                   "db_vs_naver_diff": {n: sorted((d.isoformat(), v[d], nv[d]) for d in nv if d in v and abs(v[d] - nv[d]) >= 0.005)
                                                         for n, v in dbv.items()},
                                   "focus": [{"date": fd.isoformat(), "naver": nv.get(fd), "fdr": fv.get(fd),
                                              **{f"db_{n}": v.get(fd) for n, v in dbv.items()}} for fd in focus]}
        print(code, "fdr!=naver:", diff_fdr[:5], {n: x[:3] for n, x in report["indexes"][code]["db_vs_naver_diff"].items()})
    (out / "close_basis_audit.json").write_text(json.dumps(report, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print("saved", out / "close_basis_audit.json")


if __name__ == "__main__":
    main()
