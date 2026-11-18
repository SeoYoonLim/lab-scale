"""저장된 일봉(stock_price)의 시가·고가·저가·거래량이 어느 세션(정규장·장후 종가매매·애프터마켓)에서 왔는지 분봉으로 감사한다
(2026-11-13, 50차). DB에 쓰지 않는다(READ ONLY 트랜잭션). 분봉 원천은 최근 7거래일만 있다(app/session_ohlcv.py).

  python -m scripts.audit_session_ohlcv --out <폴더>                       # 등록 종목 전부, 최근 완료 거래일 6일
  python -m scripts.audit_session_ohlcv --tickers 005930,087010 --out <폴더> --keep-raw

출력(<폴더>): session_audit_rows.csv(종목·날짜별 판정), session_audit_summary.json(자산별 집계),
--keep-raw면 raw/{ticker}.json(원본 분봉; 7거래일이 지나면 원천에서 사라지므로 보존용).
"""
import argparse
import csv
import json
import time
from collections import Counter
from datetime import date, datetime
from pathlib import Path

from sqlalchemy import create_engine, text

from app import krx_calendar, session_ohlcv as so
from app.db.session import DB_URL

FIELDS = ("ticker", "asset_type", "date", "issue", "open_is_regular", "close_is_regular_close", "high_from", "low_from",
          "volume", "volume_regular", "volume_closing_price", "volume_after_market", "volume_unattributed", "volume_over_regular_pct")


def default_days(now: datetime, n: int = so.MINUTE_HISTORY_TRADING_DAYS - 1) -> list[date]:
    """분봉이 끝난(day_complete) 최근 거래일 n개(오래된 날 먼저)."""
    d = krx_calendar.last_trading_day(krx_calendar._kst(now).date())
    out = []
    while len(out) < n:
        if so.day_complete(d, now):
            out.append(d)
        d = krx_calendar.previous_trading_day(d)
    return sorted(out)


def load_rows(url: str, tickers: list[str] | None, start: date, end: date) -> dict:
    eng = create_engine(url, connect_args={"connect_timeout": 10})
    sql = ("SELECT c.ticker, c.asset_type, p.price_date, p.open_price, p.high_price, p.low_price, p.close_price, p.volume "
           "FROM stock_price p JOIN company c ON c.id = p.company_id WHERE p.price_date BETWEEN :s AND :e")
    params: dict = {"s": start, "e": end}
    if tickers:
        sql += " AND c.ticker = ANY(:t)"
        params["t"] = tickers
    with eng.connect() as c:
        c.execute(text("SET TRANSACTION READ ONLY"))
        rows = c.execute(text(sql), params).all()
    eng.dispose()
    out: dict = {}
    for t, a, d, o, h, lo, cl, v in rows:
        out.setdefault(t, {"asset_type": a, "days": {}})["days"][d] = {
            "open": float(o), "high": float(h), "low": float(lo), "close": float(cl), "volume": None if v is None else int(v)}
    return out


def summarize(rows: list[dict]) -> dict:
    """자산별 집계(테스트 대상)."""
    out: dict = {}
    for asset in sorted({r["asset_type"] for r in rows}):
        rs = [r for r in rows if r["asset_type"] == asset]
        ok = [r for r in rs if not r["issue"]]
        over = sorted(r["volume_over_regular_pct"] for r in ok if r.get("volume_over_regular_pct") is not None)
        out[asset] = {
            "rows": len(rs), "checked": len(ok), "issues": dict(Counter(r["issue"] for r in rs if r["issue"])),
            "open_is_regular": sum(bool(r["open_is_regular"]) for r in ok),
            "close_is_regular_close": sum(bool(r["close_is_regular_close"]) for r in ok),
            "high_from": dict(Counter(r["high_from"] for r in ok)), "low_from": dict(Counter(r["low_from"] for r in ok)),
            "volume_over_regular_pct": ({"median": over[len(over) // 2], "p90": over[int(len(over) * 0.9)], "max": over[-1]}
                                        if over else None),
        }
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--tickers", help="쉼표로 구분. 없으면 기간 안 시세가 있는 등록 종목 전부")
    ap.add_argument("--out", required=True)
    ap.add_argument("--keep-raw", action="store_true")
    ap.add_argument("--sleep", type=float, default=0.35, help="요청 간격(초)")
    args = ap.parse_args(argv)
    now = datetime.now(krx_calendar.KST)
    days = default_days(now)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    if args.keep_raw:
        (out / "raw").mkdir(exist_ok=True)
    stored = load_rows(DB_URL, args.tickers.split(",") if args.tickers else None, days[0], days[-1])
    print(f"대상 {len(stored)}종목, 날짜 {days[0]}~{days[-1]}({len(days)}거래일), 기준 시각 {now.isoformat(timespec='minutes')}")
    rows, failed = [], []
    for i, (t, info) in enumerate(sorted(stored.items())):
        try:
            bars = so.fetch_minute(t, days[0], days[-1])
        except Exception as e:  # noqa: BLE001 - 종목 하나의 실패로 멈추지 않는다
            failed.append({"ticker": t, "error": str(e)[:120]})
            continue
        if args.keep_raw:
            (out / "raw" / f"{t}.json").write_text(json.dumps(bars, ensure_ascii=False), encoding="utf-8")
        sessions = so.aggregate(bars)
        for d in days:
            if d not in info["days"]:
                continue
            st = info["days"][d]
            r = so.compare_day(st, sessions.get(d, {}), d, now)
            rows.append({"ticker": t, "asset_type": info["asset_type"], "volume": st["volume"],
                         **{k: r.get(k) for k in FIELDS if k in r}})
        if i % 50 == 0:
            print(f"  {i}/{len(stored)} {t}", flush=True)
        time.sleep(args.sleep)
    with open(out / "session_audit_rows.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    summary = {"generated_at": now.isoformat(), "days": [d.isoformat() for d in days], "failed": failed,
               "by_asset": summarize(rows)}
    (out / "session_audit_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(summary["by_asset"], ensure_ascii=False, indent=1))
    print(f"실패 {len(failed)}종목. 출력: {out}")


if __name__ == "__main__":
    main()
