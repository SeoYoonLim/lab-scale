"""universe_top{N}.json의 종목에 대해 최근 주가(OHLCV)를 FinanceDataReader로 수집해 stock_price에 upsert한다.

이미 저장된 날짜는 최신 값으로 갱신하므로 다시 실행해도 안전하다(멱등). 실패해도 다음 종목으로
계속 진행하며(자동 재시도 없음) 마지막에 실패 목록을 출력한다.
universe 파일은 커밋 대상이 아니므로 먼저 `python -m scripts.build_universe`로 생성해야 한다.

사용 예:
  python -m scripts.collect_prices --every 40 --dry-run          # DB에 쓰지 않고 시범 확인
  python -m scripts.collect_prices --days 90 --report prices_report.json
"""

import argparse
import json
import time
from pathlib import Path

from app.collectors import fetch_and_save_stock
from scripts.collect_universe import UNIVERSE_DIR, _register, _timed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--universe", default="universe_top300.json")
    ap.add_argument("--every", type=int, default=1, help="N개 간격으로 표본 추출 (시범 실행용)")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--delay", type=float, default=0.2, help="종목 사이 대기(초)")
    ap.add_argument("--days", type=int, default=90, help="조회 기간(달력 기준 일)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--report", default=None, help="결과 JSON 저장 경로")
    args = ap.parse_args()

    universe = json.loads((UNIVERSE_DIR / args.universe).read_text(encoding="utf-8"))["universe"]
    targets = universe[:: args.every]
    if args.limit is not None:
        targets = targets[: args.limit]

    print(f"대상 {len(targets)}종목 (every={args.every}, days={args.days}, delay={args.delay}s, dry_run={args.dry_run})")
    started = time.time()
    rows = []

    for i, t in enumerate(targets, 1):
        ticker, name = t["ticker"], t["name"]
        row = {"rank": t["rank"], "ticker": ticker, "name": name}

        err = _register(ticker, name) if not args.dry_run else None
        if err:
            row.update(ok=False, saved=0, fetched=0, error=f"회사 등록 실패: {err}", sec=0)
            rows.append(row)
            print(f"[{i}/{len(targets)}] {name}({ticker}) 회사 등록 실패: {err}")
            continue

        res, sec = _timed(fetch_and_save_stock, ticker, name, days=args.days, dry_run=args.dry_run)
        time.sleep(args.delay)
        row.update(ok=res.ok, saved=res.saved, fetched=res.fetched, error=res.error, sec=round(sec, 2))
        rows.append(row)
        print(f"[{i}/{len(targets)}] {name}({ticker}) {'OK' if res.ok else 'FAIL'} 조회 {res.fetched}건 / 신규 {res.saved}건 ({row['sec']}s)")

    elapsed = time.time() - started
    failures = [(r["name"], r["ticker"], r["error"]) for r in rows if not r["ok"]]
    summary = {
        "targets": len(targets),
        "ok": sum(1 for r in rows if r["ok"]),
        "failures": len(failures),
        "elapsed_sec": round(elapsed, 1),
        "sec_per_ticker": round(elapsed / max(len(targets), 1), 2),
        "rows_fetched": sum(r["fetched"] for r in rows),
        "rows_new": sum(r["saved"] for r in rows),
    }
    print("\n===== 요약 =====")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if failures:
        print("===== 실패 목록 =====")
        for name, ticker, reason in failures:
            print(f"  {name}({ticker}) {reason}")

    if args.report:
        Path(args.report).write_text(
            json.dumps({"summary": summary, "failures": failures, "rows": rows}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"결과 저장: {args.report}")


if __name__ == "__main__":
    main()
