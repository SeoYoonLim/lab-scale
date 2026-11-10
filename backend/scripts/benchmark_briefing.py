"""(E, 2026-10-28) GET /api/briefing 처리 시간 측정. captest_ 계정에 보유 N/2 + 관심 N/2 종목(시세가 있는 실제 종목)을 만들고
TestClient로 반복 호출해 p50/p95를 낸다. 쿼리 수와 가장 느린 쿼리(EXPLAIN ANALYZE)도 남긴다. 끝나면 계정과 데이터를 지운다.

    python -m scripts.benchmark_briefing --sizes 10,20 --runs 40
"""

import argparse
import json
import secrets
import statistics
import sys
import time

from fastapi.testclient import TestClient
from sqlalchemy import event, text

from app.auth import create_access_token, create_user
from app.db.session import SessionLocal, engine
from app.main import app
from app.models import Holding, User, VirtualAccount, Watchlist


def pct(xs, p):
    xs = sorted(xs)
    k = (len(xs) - 1) * p / 100
    lo, hi = int(k), min(int(k) + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def measure(n_items: int, runs: int, warmup: int = 5) -> dict:
    db = SessionLocal()
    user = create_user(db, f"captest_{secrets.token_hex(5)}", secrets.token_urlsafe(16))
    try:
        ids = [i for (i,) in db.execute(text(
            "SELECT c.id FROM company c WHERE EXISTS (SELECT 1 FROM stock_price p WHERE p.company_id = c.id) "
            "ORDER BY c.id LIMIT :n"), {"n": n_items})]
        half = n_items // 2
        db.add(VirtualAccount(device_id=user.owner_key, cash_balance=5_000_000))
        db.flush()
        for cid in ids[:half]:
            db.add(Holding(device_id=user.owner_key, company_id=cid, quantity=3, avg_price=10_000))
        for cid in ids[half:]:
            db.add(Watchlist(device_id=user.owner_key, company_id=cid))
        db.commit()

        client = TestClient(app)
        headers = {"Authorization": f"Bearer {create_access_token(user.id)}"}
        for _ in range(warmup):
            assert client.get("/api/briefing", headers=headers).status_code == 200
        times, statements = [], []
        listener = lambda conn, cur, st, params, ctx, many: statements.append((st, params))  # noqa: E731
        for i in range(runs):
            if i == 0:
                event.listen(engine, "before_cursor_execute", listener)
            t0 = time.perf_counter()
            r = client.get("/api/briefing", headers=headers)
            times.append((time.perf_counter() - t0) * 1000)
            if i == 0:
                event.remove(engine, "before_cursor_execute", listener)
            assert r.status_code == 200
        body = r.json()

        # 쿼리별 실행 시간(EXPLAIN ANALYZE, 읽기 전용 SELECT만)
        plans = []
        raw = engine.raw_connection()
        try:
            cur = raw.cursor()
            for st, params in statements:
                if not st.lstrip().upper().startswith("SELECT"):
                    continue
                cur.execute("EXPLAIN (ANALYZE, FORMAT JSON) " + st, params)
                plan = cur.fetchone()[0][0]
                plans.append({"ms": round(plan["Execution Time"], 2), "sql": " ".join(st.split())[:120]})
            raw.rollback()
        finally:
            raw.close()
        return {
            "items": n_items, "holdings": len(body["holdings"]), "watchlist": len(body["watchlist"]), "runs": runs,
            "p50_ms": round(statistics.median(times), 1), "p95_ms": round(pct(times, 95), 1),
            "max_ms": round(max(times), 1), "mean_ms": round(statistics.mean(times), 1),
            "queries_per_request": len(statements),
            "slowest_queries": sorted(plans, key=lambda p: -p["ms"])[:3],
        }
    finally:
        db.rollback()
        db.query(Watchlist).filter(Watchlist.device_id == user.owner_key).delete()
        db.query(VirtualAccount).filter(VirtualAccount.device_id == user.owner_key).delete()
        db.query(User).filter(User.id == user.id).delete()
        db.commit()
        db.close()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sizes", default="10,20")
    ap.add_argument("--runs", type=int, default=40)
    args = ap.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")
    out = [measure(int(n), args.runs) for n in args.sizes.split(",")]
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
