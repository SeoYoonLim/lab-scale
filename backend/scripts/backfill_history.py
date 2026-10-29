"""과거 시세 장기 수집(백테스트용): company 테이블 종목의 일별 시세와 코스피·코스닥 지수를 --years년치 받아 upsert한다.

- 수집 경로는 기존 것을 그대로 쓴다: app.collectors.fetch_and_save_stock / fetch_and_save_market_index
  (FinanceDataReader, http_retry의 timeout·재시도, on_conflict upsert라 다시 실행해도 같은 결과). 종목 사이에 --delay초 쉰다.
- 기간 끝: 평일 15:30(KST) 전이면 어제, 아니면 오늘. 장중(평일 09:00~15:30)에는 기본으로 실행을 거부한다
  (--allow-market-hours를 줘도 끝은 어제라 오늘 행을 건드리지 않는다).
- 순서: 최근 20거래일 평균 거래대금(종가×거래량, DB 값) 큰 종목부터. --limit N이면 상위 N개만.
- 이어하기: 종목마다 결과를 backend/backups/backfill_state.json(gitignore 대상)에 바로 적는다. --resume이면 같은 기간으로
  이미 끝난 종목은 건너뛴다. 실패한 종목은 다시 시도한다.
- 기록: 실행마다 data_refresh_log에 step='backfill' 한 행(rows_added=새 행 수, latest_date=기간 끝). /api/status의 last_refresh는
  refresh_data 단계만 읽으므로(app.data_status.REFRESH_STEPS) 영향이 없다. 종목별 결과(행 수·시작/끝 날짜·실패 사유·품질 점검)는
  --report JSON(기본 scripts/backfill_history_report.json, gitignore 대상)과 요약 출력에 남긴다.
- 품질 점검(app.price_quality): ±30% 초과 일간 변동, 0 이하 종가, 같은 종가가 5거래일 이상 이어지는 구간, 중복·역순 날짜.
  저장된 행은 날짜순으로 읽는다(삽입 순서는 업서트 때문에 날짜순이 아니다). 중복은 유니크 제약(company_id, price_date)으로
  DB에 생길 수 없고, 역순은 날짜순 조회라 0이다. 그래도 점검 결과에 숫자로 남긴다.

사용 예:
  python -m scripts.backfill_history --dry-run --limit 3          # DB에 쓰지 않고 시범
  python -m scripts.backfill_history --years 3                    # 전체 종목 + 지수
  python -m scripts.backfill_history --resume                     # 중단된 곳부터
  python -m scripts.backfill_history --tickers 005930,069500 --years 5
  python -m scripts.backfill_history --end 2026-10-28             # 끝 날짜를 당긴다(오늘 행은 refresh_data에 맡길 때)
"""

import argparse
import json
import time
from datetime import date, datetime, time as dtime, timedelta
from pathlib import Path

from sqlalchemy import func, select, text

from app.collectors import fetch_and_save_market_index, fetch_and_save_stock
from app.db.session import SessionLocal
from app.models import Company, DataRefreshLog, MarketIndex, StockPrice
from app.models.market_index import MARKET_INDEXES
from app.price_quality import check_series
from scripts.refresh_data import KST, is_market_hours

BACKEND = Path(__file__).resolve().parents[1]
STATE_FILE = BACKEND / "backups" / "backfill_state.json"
DEFAULT_REPORT = BACKEND / "scripts" / "backfill_history_report.json"
MARKET_CLOSE = dtime(15, 30)
MAX_YEARS = 10


def period(now: datetime, years: int) -> tuple[date, date]:
    """(시작, 끝). 끝은 평일 15:30 전이면 어제(오늘 행은 장중 값이거나 아직 없다), 아니면 오늘."""
    now = now.astimezone(KST)
    end = now.date() - timedelta(days=1) if now.weekday() < 5 and now.time() < MARKET_CLOSE else now.date()
    try:
        start = end.replace(year=end.year - years)
    except ValueError:  # 2월 29일
        start = end.replace(year=end.year - years, day=28)
    return start, end


def ordered_targets(db, tickers: list[str] | None) -> list[tuple[str, str]]:
    """(ticker, name). 최근 20거래일 평균 거래대금이 큰 순(시세가 없는 종목은 뒤, 같으면 ticker 순)."""
    recent = select(StockPrice.price_date).distinct().order_by(StockPrice.price_date.desc()).limit(20).subquery()
    turnover = (
        select(StockPrice.company_id, func.avg(StockPrice.close_price * StockPrice.volume).label("v"))
        .where(StockPrice.price_date.in_(select(recent.c.price_date)))
        .group_by(StockPrice.company_id)
        .subquery()
    )
    q = select(Company.ticker, Company.name, turnover.c.v).outerjoin(turnover, turnover.c.company_id == Company.id)
    if tickers:
        q = q.where(Company.ticker.in_(tickers))
    rows = db.execute(q).all()
    rows.sort(key=lambda r: (r.v is None, -(float(r.v) if r.v is not None else 0), r.ticker))
    return [(r.ticker, r.name) for r in rows]


def load_state(start: date, end: date, resume: bool) -> dict:
    key = f"{start.isoformat()}~{end.isoformat()}"
    if resume and STATE_FILE.exists():
        state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        if state.get("range") == key:
            return state
        print(f"[resume] 저장된 진행 상태의 기간({state.get('range')})이 이번 기간({key})과 달라 처음부터 합니다.")
    return {"range": key, "done": {}}


def save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(exist_ok=True)
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(STATE_FILE)


def stored_series(db, ticker: str) -> list[tuple[date, float]]:
    return [
        (d, float(c))
        for d, c in db.execute(
            select(StockPrice.price_date, StockPrice.close_price)
            .join(Company, Company.id == StockPrice.company_id)
            .where(Company.ticker == ticker)
            .order_by(StockPrice.price_date)
        )
    ]


def sizes(db) -> dict:
    return dict(db.execute(text(
        "SELECT pg_database_size(current_database()) AS db, pg_total_relation_size('stock_price') AS stock_price, "
        "pg_total_relation_size('market_index') AS market_index"
    )).mappings().one())


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--years", type=int, default=3)
    ap.add_argument("--tickers", default=None, help="쉼표 구분 종목코드(기본: company 전체)")
    ap.add_argument("--limit", type=int, default=None, help="거래대금 상위 N개만")
    ap.add_argument("--resume", action="store_true", help="같은 기간으로 이미 끝난 종목은 건너뜀")
    ap.add_argument("--delay", type=float, default=0.5, help="종목 사이 대기(초)")
    ap.add_argument("--skip-index", action="store_true", help="지수(코스피·코스닥)는 받지 않음")
    ap.add_argument("--dry-run", action="store_true", help="DB에 쓰지 않음(외부 조회만)")
    ap.add_argument("--report", default=str(DEFAULT_REPORT))
    ap.add_argument("--allow-market-hours", action="store_true", help="장중에도 실행(끝은 어제)")
    ap.add_argument("--end", default=None, help="기간 끝 YYYY-MM-DD(기본 계산값보다 늦을 수 없다)")
    args = ap.parse_args(argv)

    if not 1 <= args.years <= MAX_YEARS:
        raise SystemExit(f"--years는 1~{MAX_YEARS}입니다.")
    now = datetime.now(KST)
    if is_market_hours(now) and not args.allow_market_hours:
        raise SystemExit("장중(평일 09:00~15:30 KST)에는 실행하지 않습니다. 장 마감 뒤에 실행하거나 --allow-market-hours를 주세요.")
    start, end = period(now, args.years)
    if args.end:
        wanted = date.fromisoformat(args.end)
        if wanted > end:
            raise SystemExit(f"--end는 {end} 이전이어야 합니다(장중·미래 행을 받지 않는다).")
        start, end = period(datetime.combine(wanted, dtime(18, 0), tzinfo=KST), args.years)
    tickers = [t.strip() for t in args.tickers.split(",") if t.strip()] if args.tickers else None

    db = SessionLocal()
    try:
        targets = ordered_targets(db, tickers)
        size_before = sizes(db)
    finally:
        db.close()
    if tickers:
        missing = sorted(set(tickers) - {t for t, _ in targets})
        if missing:
            print(f"company 테이블에 없는 종목은 건너뜁니다: {', '.join(missing)}")
    if args.limit is not None:
        targets = targets[: args.limit]

    state = load_state(start, end, args.resume)
    todo = [(t, n) for t, n in targets if not state["done"].get(t, {}).get("ok")]
    print(f"기간 {start} ~ {end}, 대상 {len(targets)}종목(남은 {len(todo)}), delay {args.delay}s, dry_run={args.dry_run}")

    started_at = datetime.now(KST)
    t0 = time.monotonic()
    results = []
    for i, (ticker, name) in enumerate(todo, 1):
        res = fetch_and_save_stock(ticker, name, dry_run=args.dry_run, start_date=start.isoformat(), end_date=end.isoformat())
        db = SessionLocal()
        try:
            series = stored_series(db, ticker)
        finally:
            db.close()
        in_range = [d for d, _ in series if start <= d <= end]
        row = {
            "ticker": ticker, "name": name, "ok": res.ok, "fetched": res.fetched, "new": res.saved,
            "first_date": min(in_range).isoformat() if in_range else None,
            "last_date": max(in_range).isoformat() if in_range else None,
            "rows_in_range": len(in_range), "error": res.error,
            "issues": [x.as_dict() for x in check_series(series)],
        }
        results.append(row)
        if not args.dry_run:
            state["done"][ticker] = {k: row[k] for k in ("ok", "fetched", "new", "first_date", "last_date", "error")}
            save_state(state)
        print(f"[{i}/{len(todo)}] {name}({ticker}) {'OK' if res.ok else 'FAIL'} 조회 {res.fetched} / 신규 {res.saved} "
              f"/ {row['first_date']}~{row['last_date']} / 점검 {len(row['issues'])}건")
        time.sleep(args.delay)

    index_rows = []
    if not args.skip_index:
        for code, label in MARKET_INDEXES.items():
            res = fetch_and_save_market_index(code, dry_run=args.dry_run, start_date=start.isoformat(), end_date=end.isoformat())
            db = SessionLocal()
            try:
                series = [(d, float(c)) for d, c in db.execute(
                    select(MarketIndex.price_date, MarketIndex.close_price).where(MarketIndex.index_code == code).order_by(MarketIndex.price_date))]
            finally:
                db.close()
            in_range = [d for d, _ in series if start <= d <= end]
            index_rows.append({"index_code": code, "name": label, "ok": res.ok, "fetched": res.fetched, "new": res.saved,
                               "source": res.source, "error": res.error,
                               "first_date": min(in_range).isoformat() if in_range else None,
                               "last_date": max(in_range).isoformat() if in_range else None,
                               "issues": [x.as_dict() for x in check_series(series)]})
            print(f"{label}({code}) {'OK' if res.ok else 'FAIL'} 조회 {res.fetched} / 신규 {res.saved} / 소스 {res.source}")
            time.sleep(args.delay)

    elapsed = time.monotonic() - t0
    db = SessionLocal()
    try:
        size_after = sizes(db)
        failures = [r for r in results if not r["ok"]] + [r for r in index_rows if not r["ok"]]
        new_rows = sum(r["new"] for r in results) + sum(r["new"] for r in index_rows)
        if not args.dry_run and (results or index_rows):
            all_failed = bool(failures) and len(failures) == len(results) + len(index_rows)
            db.add(DataRefreshLog(
                step="backfill", status="failed" if all_failed else "success", started_at=started_at,
                finished_at=datetime.now(KST), rows_added=new_rows, latest_date=end,
                error_type=("PartialFailure" if failures and not all_failed else ("AllFailed" if all_failed else None)),
            ))
            db.commit()
    finally:
        db.close()

    jumps = [{"ticker": r["ticker"], "name": r["name"], **x} for r in results for x in r["issues"] if x["kind"] == "jump"]
    summary = {
        "range": f"{start}~{end}", "targets": len(targets), "attempted": len(results),
        "ok": sum(1 for r in results if r["ok"]), "failed": [(r["ticker"], r["error"]) for r in failures],
        "rows_fetched": sum(r["fetched"] for r in results), "rows_new": new_rows,
        "elapsed_sec": round(elapsed, 1),
        "size_delta_bytes": {k: size_after[k] - size_before[k] for k in size_after},
        "size_after_bytes": size_after,
        "issues_by_kind": {k: sum(1 for r in results for x in r["issues"] if x["kind"] == k)
                           for k in ("jump", "duplicate_date", "out_of_order", "non_positive", "flat_run")},
        "jumps": jumps,
        "coverage": {"done_ok": sum(1 for v in state["done"].values() if v.get("ok")), "targets": len(targets)},
    }
    print("\n===== 요약 =====")
    print(json.dumps({k: v for k, v in summary.items() if k != "jumps"}, ensure_ascii=False, indent=1))
    for j in jumps:
        print(f"  불연속 의심 {j['name']}({j['ticker']}) {j['date']} {j['detail']}")
    Path(args.report).write_text(json.dumps({"summary": summary, "stocks": results, "indexes": index_rows},
                                            ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"결과 저장: {args.report}")
    raise SystemExit(1 if failures and len(failures) == len(results) + len(index_rows) else 0)


if __name__ == "__main__":
    main()
