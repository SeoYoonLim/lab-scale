"""universe_top{N}.json의 종목에 대해 뉴스 + DART 공시를 수집한다.

universe_etf{N}.json(scripts/build_etf_universe.py)처럼 항목에 "asset_type": "etf"가 있으면 ETF로 등록하고, DART 공시는
수집하지 않고(ETF는 공시 대상이 아니다) 뉴스만 수집한다. ETF 뉴스는 이름으로 최신순·관련도순 두 번 검색해서 제목·본문에
그 ETF 이름이 직접 나오는 기사만 저장한다(app.collectors._etf_news_candidates).

회사는 항상 (ticker, name) 쌍으로 먼저 등록한 뒤 수집하고, 종목별 결과/소요시간을 기록한다.
실패해도 다음 종목으로 계속 진행하며(자동 재시도 없음) 마지막에 실패 목록을 출력한다.

universe 파일은 커밋 대상이 아니므로 먼저 `python -m scripts.build_universe`로 생성해야 한다.

사용 예:
  python -m scripts.collect_universe --every 20                # 20개 간격 표본(시범)
  python -m scripts.collect_universe --dry-run --limit 3         # DB에 쓰지 않고 확인
  python -m scripts.collect_universe --report report.json        # 전체 실행 + 결과 JSON 저장
  python -m scripts.collect_universe --universe universe_etf50.json   # ETF: 뉴스만 수집
  python -m scripts.collect_universe --news-only --names SK,GS       # 공시 없이 지정 종목 뉴스만
  python -m scripts.collect_universe --news-only --state backups/news_state.json   # 끊기면 같은 명령으로 이어서

네이버가 401·403·429를 돌려주면(재시도 후에도) 다음 종목으로 넘어가지 않고 바로 멈춘다(종료 코드 3).
"""

import argparse
import json
import re
import sys
import time
from pathlib import Path

from app import collectors
from app.collectors import (
    CollectResult,
    _safe_error,
    fetch_and_save_disclosures,
    fetch_and_save_news,
    get_or_create_company,
)
from app.db.session import SessionLocal
from sqlalchemy import text

UNIVERSE_DIR = Path(__file__).resolve().parent

BLOCKED_EXIT_CODE = 3
_BLOCKED_STATUS_RE = re.compile(r"\b(?:401|403|429)\b")


def is_blocked_error(error: str | None) -> bool:
    """다음 종목을 계속 요청해도 소용없는 응답(인증 실패·호출 제한)인지. 요청 오류 문구의 상태 코드로 본다."""
    return bool(error and _BLOCKED_STATUS_RE.search(error))


def _load_done(path: str | None) -> set[str]:
    if not path or not Path(path).exists():
        return set()
    return set(json.loads(Path(path).read_text(encoding="utf-8"))["done"])


def _save_done(path: str | None, done: set[str]) -> None:
    if path:
        Path(path).write_text(json.dumps({"done": sorted(done)}, ensure_ascii=False), encoding="utf-8")


def _register(ticker: str, name: str, asset_type: str = "stock") -> str | None:
    """(ticker, name) 쌍으로 회사를 등록한다. 실패 사유를 반환하고, 성공하면 None."""
    db = SessionLocal()
    try:
        get_or_create_company(db, ticker, name, asset_type=asset_type)
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
    ap.add_argument("--news-only", action="store_true", help="공시는 수집하지 않는다")
    ap.add_argument("--names", default=None, help="쉼표로 구분한 종목명만 수집")
    ap.add_argument("--state", default=None, help="끝난 종목을 기록하는 파일. 다시 실행하면 그 종목은 건너뛴다")
    args = ap.parse_args()

    universe = json.loads((UNIVERSE_DIR / args.universe).read_text(encoding="utf-8"))["universe"]
    targets = universe[:: args.every]
    if args.names:
        wanted = {n.strip() for n in args.names.split(",")}
        targets = [t for t in targets if t["name"] in wanted]
    if args.limit is not None:
        targets = targets[: args.limit]
    done = _load_done(args.state)
    targets = [t for t in targets if t["ticker"] not in done]

    print(f"대상 {len(targets)}종목 (every={args.every}, delay={args.delay}s, dry_run={args.dry_run}, 이미 끝난 종목 {len(done)}개)")
    started = time.time()
    requests_before = collectors.naver_requests
    rows = []
    blocked = None

    for i, t in enumerate(targets, 1):
        ticker, name = t["ticker"], t["name"]
        asset_type = t.get("asset_type", "stock")
        row = {"rank": t["rank"], "ticker": ticker, "name": name, "asset_type": asset_type}
        t0 = time.time()

        err = _register(ticker, name, asset_type) if not args.dry_run else None
        if err:
            row["register_error"] = err
            row["news"] = row["disclosure"] = None
            print(f"[{i}/{len(targets)}] {name}({ticker}) 회사 등록 실패: {err}")
            rows.append(row)
            continue

        news, news_sec = _timed(
            fetch_and_save_news, name, display=args.display, dry_run=args.dry_run, ticker=ticker,
            etf=asset_type == "etf",
        )
        time.sleep(args.delay)
        if is_blocked_error(news.error):
            blocked = news.error
            print(f"[{i}/{len(targets)}] {name}({ticker}) 수집 중단(FAIL): {blocked}")
            break
        if asset_type == "etf" or args.news_only:
            # ETF는 DART 공시 대상이 아니라서 건너뛴다(실패가 아니라 "해당 없음"이다).
            disc, disc_sec = CollectResult(ok=True), 0.0
        else:
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
        if news.ok and disc.ok:
            done.add(ticker)
            _save_done(args.state, done)
        print(
            f"[{i}/{len(targets)}] {name}({ticker}) "
            f"뉴스 {'OK' if news.ok else 'FAIL'}+{news.saved} / "
            f"{'공시 건너뜀' if asset_type == 'etf' or args.news_only else '공시 ' + ('OK' if disc.ok else 'FAIL') + '+' + str(disc.saved)} "
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
        "naver_requests": collectors.naver_requests - requests_before,
        "blocked": blocked,
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
    if blocked:
        sys.exit(BLOCKED_EXIT_CODE)


LOCK_BUSY_EXIT_CODE = 2


def run_cli() -> None:
    """명령줄 진입점(2026-11-09, 40차): DB에 쓰는 실행은 데이터 갱신(scripts/refresh_data)과 같은 advisory lock을 잡는다.
    뉴스·공시는 DB 유니크 제약 없이 "있는지 확인 → 넣기"로 중복을 막으므로, 직접 실행한 수집이 정기 갱신이나 격리 재처리와 겹치면
    같은 기사·공시가 두 번 들어갈 수 있었다. refresh_data는 락을 잡은 채 main()을 직접 부르므로 이 함수를 거치지 않는다.
    --dry-run은 쓰지 않으므로 락을 잡지 않는다."""
    if "--dry-run" in sys.argv[1:]:
        main()
        return
    from app import refresh_runs

    try:
        lock = refresh_runs.acquire_lock()
    except refresh_runs.LockBusy:
        print("데이터 갱신(또는 다른 수집·재처리)이 실행 중이라 수집하지 않았습니다. 끝난 뒤 다시 실행하세요.")
        sys.exit(LOCK_BUSY_EXIT_CODE)
    try:
        main()
    finally:
        lock.release()


if __name__ == "__main__":
    run_cli()
