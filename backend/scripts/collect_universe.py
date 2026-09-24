"""universe_top{N}.json의 종목에 대해 뉴스 + DART 공시를 수집한다.

회사는 항상 (ticker, name) 쌍으로 먼저 등록한 뒤 수집하고, 종목별 결과/소요시간을 기록한다.
실패해도 다음 종목으로 계속 진행하며(자동 재시도 없음) 마지막에 실패 목록을 출력한다.

universe 파일은 커밋 대상이 아니므로 먼저 `python -m scripts.build_universe`로 생성해야 한다.

사용 예:
  python -m scripts.collect_universe --every 20                # 20개 간격 표본(시범)
  python -m scripts.collect_universe --dry-run --limit 3         # DB에 쓰지 않고 확인
  python -m scripts.collect_universe --report report.json        # 전체 실행 + 결과 JSON 저장
"""

import argparse
import json
import time
from pathlib import Path

from app.collectors import (
    _safe_error,
    fetch_and_save_disclosures,
    fetch_and_save_news,
    get_or_create_company,
)
from app.db.session import SessionLocal
from sqlalchemy import text

UNIVERSE_DIR = Path(__file__).resolve().parent


def _register(ticker: str, name: str) -> str | None:
    """(ticker, name) 쌍으로 회사를 등록한다. 실패 사유를 반환하고, 성공하면 None."""
    db = SessionLocal()
    try:
        get_or_create_company(db, ticker, name)
        db.commit()
        return None
    except Exception as e:
        db.rollback()
        return _safe_error(e)
    finally:
        db.close()


def _timed(fn, *args, **kwargs):
    t0 = time.time()
    result = fn(*args, **kwargs)
    return result, time.time() - t0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--universe", default="universe_top300.json")
    ap.add_argument("--every", type=int, default=1, help="N개 간격으로 표본 추출 (시범 실행용)")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--delay", type=float, default=0.2, help="API 호출 사이 대기(초)")
    ap.add_argument("--days", type=int, default=90, help="공시 조회 기간(일)")
    ap.add_argument("--display", type=int, default=10, help="종목당 뉴스 건수")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--report", default=None, help="결과 JSON 저장 경로")
    args = ap.parse_args()

    universe = json.loads((UNIVERSE_DIR / args.universe).read_text(encoding="utf-8"))["universe"]
    targets = universe[:: args.every]
    if args.limit is not None:
        targets = targets[: args.limit]

    print(f"대상 {len(targets)}종목 (every={args.every}, delay={args.delay}s, dry_run={args.dry_run})")
    started = time.time()
    rows = []

    for i, t in enumerate(targets, 1):
        ticker, name = t["ticker"], t["name"]
        row = {"rank": t["rank"], "ticker": ticker, "name": name}
        t0 = time.time()

        err = _register(ticker, name) if not args.dry_run else None
        if err:
            row["register_error"] = err
            row["news"] = row["disclosure"] = None
            print(f"[{i}/{len(targets)}] {name}({ticker}) 회사 등록 실패: {err}")
            rows.append(row)
            continue

        news, news_sec = _timed(
            fetch_and_save_news, name, display=args.display, dry_run=args.dry_run, ticker=ticker
        )
        time.sleep(args.delay)
        disc, disc_sec = _timed(fetch_and_save_disclosures, name, days=args.days, dry_run=args.dry_run)
        time.sleep(args.delay)

        for key, res, sec in (("news", news, news_sec), ("disclosure", disc, disc_sec)):
            row[key] = {
                "ok": res.ok,
                "saved": res.saved,
                "fetched": res.fetched,
                "error": res.error,
                "sec": round(sec, 2),
            }
        row["sec"] = round(time.time() - t0, 2)
        rows.append(row)
        print(
            f"[{i}/{len(targets)}] {name}({ticker}) "
            f"뉴스 {'OK' if news.ok else 'FAIL'}+{news.saved} / 공시 {'OK' if disc.ok else 'FAIL'}+{disc.saved} "
            f"({row['sec']}s)"
        )

    elapsed = time.time() - started

    # 뉴스 관련도 기록용 (필터링은 하지 않음): 종목별 DB 저장 뉴스 중 제목/본문에 회사명이 포함된 비율.
    # 이번 실행 이전에 저장된 뉴스도 포함해서 집계한다.
    if not args.dry_run:
        db = SessionLocal()
        try:
            stats = {
                r.ticker: (r.total, r.hit)
                for r in db.execute(
                    text(
                        """
                        select c.ticker, count(n.id) as total,
                               coalesce(sum((position(c.name in n.title) > 0
                                             or position(c.name in coalesce(n.content, '')) > 0)::int), 0) as hit
                        from company c join news n on n.company_id = c.id
                        group by c.ticker
                        """
                    )
                )
            }
        finally:
            db.close()
        for r in rows:
            if r.get("news"):
                total, hit = stats.get(r["ticker"], (0, 0))
                r["news"]["db_total"] = total
                r["news"]["name_hits"] = int(hit)
                r["news"]["name_ratio"] = round(hit / total, 3) if total else None

    failures = []
    for r in rows:
        if r.get("register_error"):
            failures.append((r["name"], r["ticker"], "register", r["register_error"]))
            continue
        for key in ("news", "disclosure"):
            if not r[key]["ok"]:
                failures.append((r["name"], r["ticker"], key, r[key]["error"]))

    summary = {
        "targets": len(targets),
        "elapsed_sec": round(elapsed, 1),
        "sec_per_ticker": round(elapsed / max(len(targets), 1), 2),
        "news_ok": sum(1 for r in rows if r.get("news") and r["news"]["ok"]),
        "disclosure_ok": sum(1 for r in rows if r.get("disclosure") and r["disclosure"]["ok"]),
        "news_saved": sum(r["news"]["saved"] for r in rows if r.get("news")),
        "disclosure_saved": sum(r["disclosure"]["saved"] for r in rows if r.get("disclosure")),
        "failures": len(failures),
    }
    print("\n===== 요약 =====")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if failures:
        print("===== 실패 목록 =====")
        for name, ticker, kind, reason in failures:
            print(f"  {name}({ticker}) [{kind}] {reason}")

    if args.report:
        Path(args.report).write_text(
            json.dumps(
                {"summary": summary, "failures": failures, "rows": rows}, ensure_ascii=False, indent=2
            ),
            encoding="utf-8",
        )
        print(f"결과 저장: {args.report}")


if __name__ == "__main__":
    main()
