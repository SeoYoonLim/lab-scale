"""뉴스·공시 누락 구간 복구 도구(62차, 2026-11-15). 자세한 설명: docs/NEWS_DISCLOSURE_RECOVERY_PLAN_62.md.

모드(--mode):
- plan(기본): **외부 요청 없음, DB 쓰기 없음.** DB를 읽기 전용으로 읽어 대상·기존 건수·요청량 상한·예상 신규 공시 수를 출력한다.
- dry-run: **네이버·DART에 실제로 요청한다**(--allow-network 필요). DB에는 쓰지 않는다(새로 저장될 건수만 센다).
- apply: 외부 요청 + DB 쓰기(--allow-network, --confirm-database ai_investment 필요). 쓰는 표: news, disclosure,
  data_ingestion_quarantine(저장 전 검증 기록), data_refresh_run·data_refresh_log(실행 기록, 단계 이름 news_recovery·disclosure_recovery).

안전 장치:
- dry-run·apply는 DATABASE_URL의 DB 이름이 ai_investment일 때만 돈다(그 밖의 DB는 거부). apply는 연결 뒤 current_database()도 다시 본다.
- 기존 뉴스·공시를 지우거나 고치지 않는다. 이미 있는 (종목, URL)·접수번호는 건너뛴다(같은 실행을 다시 해도 0건).
- 종목은 새로 만들지 않는다(등록되지 않은 종목은 실패로 기록).
- apply는 refresh_data와 같은 advisory lock을 잡는다(동시에 돌지 않는다). --state 파일로 끝난 종목을 기억해 실패 뒤 다시 실행할 수 있다.
- 네이버 401·403·429(인증·호출 제한)가 나오면 나머지 종목을 요청하지 않고 멈춘다. --naver-budget(기본 20,000)을 넘기 전에 멈춘다.
- DART 020(요청 제한)·010/011/012/901(키·인증)도 멈춘다(63차에 인증 오류 추가).
- --relevant-only(63차): 관련성 낮음(is_relevant_news=False) 기사는 저장하지 않는다. 기본은 기존 수집처럼 is_relevant=false로 저장한다.

예:
  python -m scripts.recover_news_disclosures                                  # plan
  python -m scripts.recover_news_disclosures --mode dry-run --allow-network --report r.json
  python -m scripts.recover_news_disclosures --mode apply --allow-network --confirm-database ai_investment \\
      --state backups/recovery62_state.json --report backups/recovery62_report.json
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from app import collectors as C, news_recovery as R

UNIVERSE_DIR = Path(__file__).resolve().parent
UNIVERSES = ("universe_top300.json", "universe_etf50.json")
DEFAULT_START = date(2026, 11, 4)  # 61차: 뉴스·공시 마지막 수집 2026-11-03 19:54 KST
DEV_DB = "ai_investment"
_BLOCKED = re.compile(r"\b(?:401|403|429)\b")
# DART 인증·키 오류(010 미등록 키, 011 사용할 수 없는 키, 012 접근할 수 없는 IP, 901 보유기간 만료 키): 다른 종목도 같은 결과라 멈춘다(63차)
_DART_AUTH = re.compile(r"\[(?:010|011|012|901)\]")
EST_SEC_PER_REQUEST = 0.35  # 요청 1건 응답 시간 가정(지연 별도). 61차 기록: 350종목 뉴스+공시 단계 약 6분
TABLES_WRITTEN = ("news", "disclosure", "data_ingestion_quarantine", "data_refresh_run", "data_refresh_log")  # apply만 쓴다


class Refused(SystemExit):
    pass


def database_name(url: str) -> str | None:
    from sqlalchemy.engine import make_url

    return make_url(url).database


def guard(mode: str, url: str, allow_network: bool, confirm: str | None) -> None:
    """실행 전 확인. 문제가 있으면 Refused(종료 코드 2)."""
    if mode == "plan":
        return
    if not allow_network:
        raise Refused(f"--mode {mode}는 네이버·DART에 실제로 요청한다. --allow-network를 함께 주어야 한다")
    name = database_name(url)
    if name != DEV_DB:
        raise Refused(f"대상 DB가 {name!r}이다. 이 도구는 {DEV_DB}만 대상으로 한다")
    if mode == "apply" and confirm != DEV_DB:
        raise Refused(f"--mode apply는 --confirm-database {DEV_DB}가 필요하다")


def load_targets(universes=UNIVERSES, tickers: list[str] | None = None) -> list[dict]:
    out, seen = [], set()
    for f in universes:
        for t in json.loads((UNIVERSE_DIR / f).read_text(encoding="utf-8"))["universe"]:
            if t["ticker"] in seen:
                continue
            seen.add(t["ticker"])
            out.append({"ticker": t["ticker"], "name": t["name"], "asset_type": t.get("asset_type", "stock")})
    if tickers:
        want = set(tickers)
        out = [t for t in out if t["ticker"] in want]
    return out


# ---------- plan(외부 요청 없음) ----------

def build_plan(db, targets: list[dict], start: date, end: date, max_pages: int, disc_days: int, delay: float,
               naver_budget: int) -> dict:
    """DB를 읽기만 한다. 외부 요청을 하지 않는다."""
    from sqlalchemy import func, select

    from app import krx_calendar
    from app.models import Company, Disclosure, News

    since, until = R.kst_window(start, end)
    tick = [t["ticker"] for t in targets]
    companies = {c.ticker: c for c in db.execute(select(Company).where(Company.ticker.in_(tick))).scalars()}
    missing = [t for t in tick if t not in companies]
    ids = [c.id for c in companies.values()]
    news_in = dict(db.execute(select(News.company_id, func.count()).where(News.company_id.in_(ids), News.published_at >= since,
                                                                          News.published_at < until).group_by(News.company_id)).all())
    stocks = [companies[t] for t in tick if t in companies and companies[t].asset_type == "stock"]
    disc_since = datetime.combine(end - timedelta(days=disc_days - 1), datetime.min.time(), R.KST)
    disc_in = dict(db.execute(select(Disclosure.company_id, func.count()).where(Disclosure.company_id.in_([c.id for c in stocks]),
                                                                                Disclosure.disclosed_at >= disc_since)
                              .group_by(Disclosure.company_id)).all())
    # 예상 신규 공시: 시작 전 20거래일의 거래일당 접수 건수(같은 대상 종목) x 기간의 거래일 수
    before = krx_calendar.trading_days(start - timedelta(days=40), start - timedelta(days=1), include_unknown=False)[-20:]
    kst_day = func.date(func.timezone("Asia/Seoul", Disclosure.disclosed_at)).label("d")
    per_day = dict(db.execute(select(kst_day, func.count())
                              .where(Disclosure.company_id.in_([c.id for c in stocks]),
                                     Disclosure.disclosed_at >= datetime.combine(before[0], datetime.min.time(), R.KST),
                                     Disclosure.disclosed_at < since)
                              .group_by(kst_day)).all()) if before else {}
    daily = [per_day.get(d, 0) for d in before]
    window_days = krx_calendar.trading_days(start, end, include_unknown=True)
    est = {"trading_days_in_window": len(window_days), "basis_trading_days": len(daily),
           "per_day_mean": round(sum(daily) / len(daily), 1) if daily else None,
           "per_day_min": min(daily) if daily else None, "per_day_max": max(daily) if daily else None}
    if daily:
        est.update(expected_new=round(est["per_day_mean"] * len(window_days)), expected_new_low=min(daily) * len(window_days),
                   expected_new_high=max(daily) * len(window_days))
    expected_new_per_co = (est.get("expected_new") or 0) / max(1, len(stocks))
    dart_pages = sum(max(1, math.ceil((disc_in.get(c.id, 0) + expected_new_per_co) / 100)) for c in stocks)
    news_targets = len(tick) - len(missing)
    naver_min, naver_max = news_targets, news_targets * min(max_pages, R.MAX_PAGES)
    dart_total = dart_pages + (1 if stocks else 0)  # + corpCode.xml 1번
    return {
        "window": {"start": start.isoformat(), "end": end.isoformat(), "since_kst": since.isoformat(), "until_kst": until.isoformat()},
        "targets": {"total": len(tick), "stocks": len(stocks), "etfs": news_targets - len(stocks), "not_registered": missing},
        "news": {"existing_in_window": sum(news_in.values()), "companies_with_existing": len(news_in),
                 "requests_min": naver_min, "requests_max": naver_max, "max_pages_per_company": min(max_pages, R.MAX_PAGES),
                 "max_results_per_company": min(max_pages, R.MAX_PAGES) * R.PAGE_SIZE, "naver_budget": naver_budget,
                 "naver_daily_limit": R.NAVER_DAILY_LIMIT, "within_budget": naver_max <= naver_budget,
                 "expected_new": "알 수 없음(외부 요청 없이 기사 수를 알 수 없다. dry-run으로 센다)",
                 "new_upper_bound": naver_max * R.PAGE_SIZE},
        "disclosures": {"days": disc_days, "existing_in_days": sum(disc_in.values()), "requests_estimate": dart_total,
                        "estimate": est},
        "time_estimate_min": {
            "low": round((naver_min + dart_total) * (EST_SEC_PER_REQUEST + delay) / 60, 1),
            "high": round((naver_max + dart_total) * (EST_SEC_PER_REQUEST + delay) / 60, 1)},
        "tables_written_on_apply": list(TABLES_WRITTEN),
        "tables_never_touched": sorted(set(db.execute(__import__("sqlalchemy").text(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public'")).scalars()) - set(TABLES_WRITTEN)),
    }


# ---------- dry-run·apply(외부 요청) ----------

def _load_state(path: str | None, key: dict) -> dict:
    if not path or not Path(path).exists():
        return {"key": key, "news": [], "disclosures": []}
    st = json.loads(Path(path).read_text(encoding="utf-8"))
    if st.get("key") != key:
        raise Refused(f"상태 파일 {path}의 기간·설정 {st.get('key')}이 이번 실행 {key}과 다르다. 다른 --state를 쓴다")
    return st


def _save_state(path: str | None, st: dict) -> None:
    if path:
        Path(path).write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")


def run(targets: list[dict], *, mode: str, start: date, end: date, today: date, max_pages: int = R.MAX_PAGES,
        disc_days: int | None = None, delay: float = 0.2, naver_budget: int = R.DEFAULT_NAVER_BUDGET,
        news: bool = True, disclosures: bool = True, state_path: str | None = None, record_history: bool = True,
        relevant_only: bool = False, fetch=None, session_factory=None, sleep=time.sleep, log=print) -> dict:
    """뉴스·공시 복구 본체. mode는 dry-run 또는 apply. fetch(네이버 한 페이지)·session_factory는 테스트에서 바꾼다.
    relevant_only: 관련성 낮음 기사를 저장하지 않는다(news_recovery.store_news)."""
    from sqlalchemy import select

    from app.models import Company

    if mode not in ("dry-run", "apply"):
        raise ValueError(mode)
    apply = mode == "apply"
    fetch = fetch or C.fetch_naver_news
    if session_factory is None:
        from app.db.session import SessionLocal as session_factory  # noqa: N813
    disc_days = disc_days or R.disclosure_days(start, today)
    since, until = R.kst_window(start, end)
    key = {"start": start.isoformat(), "end": end.isoformat(), "max_pages": max_pages, "disc_days": disc_days}
    if relevant_only:
        key["relevant_only"] = True
    state = _load_state(state_path, key) if apply else {"key": key, "news": [], "disclosures": []}
    naver0, dart0 = C.naver_requests, C.dart_requests
    rows, stopped, pages_used = [], None, 0  # pages_used: 받은 네이버 페이지 수(예산 판단용, 재시도 제외)
    run_id = None
    if apply and record_history:
        from app import refresh_runs

        db = session_factory()
        try:
            run_id = refresh_runs.start_run(db, ["news_recovery", "disclosure_recovery"], trigger="recovery")
        finally:
            db.close()
    db = session_factory()
    try:
        listed = [n for (n,) in db.execute(select(Company.name))]
        etf_names = [n for (n,) in db.execute(select(Company.name).where(Company.asset_type == "etf"))]
    finally:
        db.close()
    started = datetime.now(R.KST)
    for i, t in enumerate(targets, 1):
        if stopped:
            break
        row = {"ticker": t["ticker"], "name": t["name"], "asset_type": t["asset_type"]}
        db = session_factory()
        try:
            company = db.execute(select(Company).where(Company.ticker == t["ticker"])).scalar_one_or_none()
            if company is None:
                row["error"] = "company_not_registered"
                rows.append(row)
                log(f"[{i}/{len(targets)}] {t['name']}({t['ticker']}) 등록되지 않은 종목: 건너뜀")
                continue
            if news and t["ticker"] not in state["news"]:
                if pages_used + max_pages > naver_budget:  # 다음 종목이 최대 페이지를 써도 예산을 넘지 않을 때만
                    stopped = "naver_budget"
                    break
                scan = R.paginate(fetch, R.news_query(company), since, until, max_pages, delay=delay, sleep=sleep)
                pages_used += scan.pages + (1 if scan.error else 0)
                n = {"pages": scan.pages, "seen": scan.seen, "in_window": len(scan.items), "out_of_range": scan.out_of_range,
                     "duplicate_in_scan": scan.duplicate_in_scan, "stop": scan.stop, "truncated": scan.truncated,
                     "oldest_seen": scan.oldest_seen.astimezone(R.KST).isoformat() if scan.oldest_seen else None}
                if scan.error:
                    n.update(ok=False, error=C._API_KEY_RE.sub(r"***", scan.error))
                    if _BLOCKED.search(scan.error):
                        stopped = "naver_blocked"
                else:
                    others = [x for x in etf_names if x != company.name]
                    s = R.store_news(db, company, scan.items, dry_run=not apply, listed_names=listed, etf_names=others,
                                     relevant_only=relevant_only)
                    n.update(ok=True, **s.__dict__)
                    if apply:
                        state["news"].append(t["ticker"])
                        _save_state(state_path, state)
                row["news"] = n
                sleep(delay)
            if disclosures and company.asset_type == "stock" and t["ticker"] not in state["disclosures"] and not stopped:
                d0 = C.dart_requests
                r = C.fetch_and_save_disclosures(company.ticker, days=disc_days, dry_run=not apply)
                row["disclosures"] = {"ok": r.ok, "requests": C.dart_requests - d0, "fetched": r.fetched, "new": r.saved,
                                      "not_company_filer": r.skipped, "quarantined": r.quarantined, "warned": r.warned,
                                      "existing": max(0, r.fetched - r.skipped - r.saved - r.quarantined), "error": r.error}
                if r.ok and apply:
                    state["disclosures"].append(t["ticker"])
                    _save_state(state_path, state)
                if r.error and "020" in r.error:
                    stopped = "dart_limit"
                elif r.error and _DART_AUTH.search(r.error):
                    stopped = "dart_auth"
                sleep(delay)
        finally:
            db.close()
        rows.append(row)
        nn, dd = row.get("news") or {}, row.get("disclosures") or {}
        log(f"[{i}/{len(targets)}] {t['name']}({t['ticker']}) 뉴스 "
            + (f"{'OK' if nn.get('ok') else 'FAIL'} 새 {nn.get('new', 0)}·기존 {nn.get('existing', 0)}·{nn.get('pages', 0)}쪽·{nn.get('stop')}"
               if nn else "건너뜀")
            + " / 공시 " + (f"{'OK' if dd.get('ok') else 'FAIL'} 새 {dd.get('new', 0)}·기존 {dd.get('existing', 0)}" if dd else "건너뜀"))
    summary = summarize(rows, targets, mode, key, C.naver_requests - naver0, C.dart_requests - dart0, stopped, started)
    summary["requests"]["naver_pages"] = pages_used
    if apply and record_history:
        _record_history(session_factory, run_id, summary, end)
        summary["run_id"] = run_id
    return summary


def summarize(rows, targets, mode, key, naver_req, dart_req, stopped, started) -> dict:
    news = [r["news"] for r in rows if r.get("news")]
    disc = [r["disclosures"] for r in rows if r.get("disclosures")]
    stops: dict[str, int] = {}
    for n in news:
        stops[n["stop"]] = stops.get(n["stop"], 0) + 1
    return {
        "mode": mode, "params": key, "started_at": started.isoformat(), "finished_at": datetime.now(R.KST).isoformat(),
        "targets": len(targets), "processed": len(rows), "stopped": stopped, "requests": {"naver": naver_req, "dart": dart_req},
        "news": {"companies_ok": sum(1 for n in news if n.get("ok")), "companies_failed": sum(1 for n in news if not n.get("ok")),
                 "new": sum(n.get("new", 0) for n in news), "relevant_new": sum(n.get("relevant_new", 0) for n in news),
                 "existing": sum(n.get("existing", 0) for n in news), "quarantined": sum(n.get("quarantined", 0) for n in news),
                 "non_financial": sum(n.get("non_financial", 0) for n in news),
                 "etf_name_missing": sum(n.get("etf_name_missing", 0) for n in news),
                 "irrelevant_skipped": sum(n.get("irrelevant_skipped", 0) for n in news),
                 "truncated_companies": [r["ticker"] for r in rows if (r.get("news") or {}).get("truncated")],
                 "no_articles_in_window": [r["ticker"] for r in rows if (r.get("news") or {}).get("ok") and not r["news"]["in_window"]],
                 "stop_reasons": stops},
        "disclosures": {"companies_ok": sum(1 for d in disc if d["ok"]), "companies_failed": sum(1 for d in disc if not d["ok"]),
                        "new": sum(d["new"] for d in disc), "existing": sum(d["existing"] for d in disc),
                        "quarantined": sum(d["quarantined"] for d in disc)},
        "failed": [{"ticker": r["ticker"], "news": (r.get("news") or {}).get("error"), "disclosures": (r.get("disclosures") or {}).get("error"),
                    "error": r.get("error")} for r in rows
                   if r.get("error") or not (r.get("news") or {"ok": True}).get("ok") or not (r.get("disclosures") or {"ok": True}).get("ok")],
        "not_processed": [t["ticker"] for t in targets[len(rows):]],
        "rows": rows,
    }


def _step_status(ok: int, failed: int, stopped: str | None) -> str:
    if failed == 0 and not stopped:
        return "success"
    return "failed" if ok == 0 else "partial"


def _record_history(session_factory, run_id, summary: dict, end: date) -> None:
    """data_refresh_run(trigger=recovery)과 단계 2행(news_recovery·disclosure_recovery). 기존 단계 이름(news 등)을 쓰지 않으므로
    GET /api/status의 last_refresh·목록의 last_collection(news 단계) 뜻은 그대로다."""
    from app import refresh_runs
    from app.models import DataRefreshLog

    now = datetime.now(R.KST)
    started = datetime.fromisoformat(summary["started_at"])
    db = session_factory()
    try:
        statuses = []
        for step, part in (("news_recovery", summary["news"]), ("disclosure_recovery", summary["disclosures"])):
            if part["companies_ok"] + part["companies_failed"] == 0:
                continue
            st = _step_status(part["companies_ok"], part["companies_failed"], summary["stopped"])
            statuses.append(st)
            db.add(DataRefreshLog(step=step, status=st, started_at=started, finished_at=now, rows_added=part["new"], latest_date=end,
                                  run_id=run_id, duration_ms=round((now - started).total_seconds() * 1000),
                                  error_code=summary["stopped"] or ("partial_failures" if part["companies_failed"] else None),
                                  warning_count=part.get("quarantined", 0)))
        db.commit()
        if run_id is not None:
            note = (f"news new {summary['news']['new']} existing {summary['news']['existing']} "
                    + (f"irrelevant_skipped {summary['news']['irrelevant_skipped']} "
                       if summary["news"].get("irrelevant_skipped") else "") + "truncated "
                    f"{len(summary['news']['truncated_companies'])} / disc new {summary['disclosures']['new']} / req naver "
                    f"{summary['requests']['naver']} dart {summary['requests']['dart']} / failed {len(summary['failed'])}"
                    + (f" / stopped {summary['stopped']}" if summary["stopped"] else ""))
            run_st = refresh_runs.run_status(statuses) if statuses else "success"
            refresh_runs.finish_run(db, run_id, run_st, 0 if run_st == "success" else 1, note=note)
    finally:
        db.close()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--mode", choices=["plan", "dry-run", "apply"], default="plan")
    ap.add_argument("--start", type=date.fromisoformat, default=DEFAULT_START, help="복구 시작일(KST, 기본 2026-11-04)")
    ap.add_argument("--end", type=date.fromisoformat, default=None, help="복구 종료일(KST, 기본 오늘)")
    ap.add_argument("--tickers", default=None, help="쉼표로 구분한 종목코드만(기본: universe_top300 + universe_etf50)")
    ap.add_argument("--no-news", action="store_true")
    ap.add_argument("--no-disclosures", action="store_true")
    ap.add_argument("--max-pages", type=int, default=R.MAX_PAGES, help=f"종목당 네이버 페이지 상한(1~{R.MAX_PAGES}, 페이지당 100건)")
    ap.add_argument("--disclosure-days", type=int, default=None, help="DART 조회 기간(일, 기본 시작일~오늘, 최대 90)")
    ap.add_argument("--delay", type=float, default=0.2, help="요청 사이 대기(초)")
    ap.add_argument("--naver-budget", type=int, default=R.DEFAULT_NAVER_BUDGET, help="이 실행에서 쓸 네이버 요청 상한")
    ap.add_argument("--relevant-only", action="store_true",
                    help="관련성 낮음(is_relevant_news=False) 기사는 저장하지 않는다(기본: 기존 수집처럼 is_relevant=false로 저장)")
    ap.add_argument("--allow-network", action="store_true", help="dry-run·apply에 필요: 네이버·DART에 실제로 요청한다")
    ap.add_argument("--confirm-database", default=None, help="apply에 필요: ai_investment")
    ap.add_argument("--state", default=None, help="apply: 끝난 종목을 기록하는 파일(다시 실행하면 건너뛴다)")
    ap.add_argument("--report", default=None, help="결과 JSON 경로")
    args = ap.parse_args(argv)

    from app.db.session import SessionLocal, engine

    url = engine.url.render_as_string(hide_password=False)
    try:
        guard(args.mode, url, args.allow_network, args.confirm_database)
    except Refused as e:
        print(f"거부: {e}")
        return 2
    today = datetime.now(R.KST).date()
    end = args.end or today
    if args.start > end:
        print("거부: --start가 --end보다 늦다")
        return 2
    if not 1 <= args.max_pages <= R.MAX_PAGES:
        print(f"거부: --max-pages는 1~{R.MAX_PAGES}")
        return 2
    disc_days = args.disclosure_days or R.disclosure_days(args.start, today)
    if not 1 <= disc_days <= 90:
        print("거부: --disclosure-days는 1~90")
        return 2
    targets = load_targets(tickers=args.tickers.split(",") if args.tickers else None)
    db = SessionLocal()
    try:
        dbname = db.execute(__import__("sqlalchemy").text("SELECT current_database()")).scalar()
        print(f"대상 DB: {dbname} (호스트 {engine.url.host}:{engine.url.port}) · 모드 {args.mode} · 기간 {args.start}~{end} KST · 대상 {len(targets)}종목")
        if args.mode == "plan":
            db.execute(__import__("sqlalchemy").text("SET TRANSACTION READ ONLY"))
            out = build_plan(db, targets, args.start, end, args.max_pages, disc_days, args.delay, args.naver_budget)
        else:
            if args.mode == "apply" and dbname != DEV_DB:
                print(f"거부: 연결된 DB가 {dbname}이다")
                return 2
            out = None
    finally:
        db.rollback()
        db.close()
    if out is None:
        lock = None
        if args.mode == "apply":
            from app import refresh_runs

            try:
                lock = refresh_runs.acquire_lock()
            except refresh_runs.LockBusy as e:
                print(f"거부: {e}")
                return 2
        try:
            out = run(targets, mode=args.mode, start=args.start, end=end, today=today, max_pages=args.max_pages,
                      disc_days=disc_days, delay=args.delay, naver_budget=args.naver_budget, news=not args.no_news,
                      disclosures=not args.no_disclosures, state_path=args.state, relevant_only=args.relevant_only)
        finally:
            if lock is not None:
                lock.release()
    text = json.dumps({k: v for k, v in out.items() if k != "rows"}, ensure_ascii=False, indent=1, default=str)
    print(text)
    if args.report:
        Path(args.report).write_text(json.dumps(out, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    if args.mode != "plan" and (out.get("failed") or out.get("stopped")):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
