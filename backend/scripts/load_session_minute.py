"""보존한 분봉 원본을 세션별로 집계해 stock_price_session에 적재한다(53차, 54차에 모드·일정·출처 기록 추가). **승인 전에는 시험 DB에만.**

  # 1) 원본·세션 분류만 검증(DB는 종목 표만 읽음)
  python -m scripts.load_session_minute --database-url <시험 DB> --source <보존 폴더> --date 2026-11-13 --validate-only
  # 2) 기본: 집계·검증(쓰지 않음). --report에 종목-날짜별 집계와 출처가 남는다
  python -m scripts.load_session_minute ... --start 2026-11-06 --end 2026-11-13 --report <파일>
  # 3) 넣을·이미 있는·충돌 행 미리 보기(READ ONLY)
  python -m scripts.load_session_minute ... --dry-run
  # 4) 적재(시험 DB 이름을 한 번 더 적는다, 출처 기록 필수)
  python -m scripts.load_session_minute ... --load --confirm-database ai_investment_session_pipeline54 --report <파일>
  # 5) DB 행을 원본에서 다시 계산한 값과 대조(READ ONLY)
  python -m scripts.load_session_minute ... --verify-only

- 분류·집계: app/session_bars.py(+ 거래일별 일정 app/session_schedule.py). 분류할 수 없는 봉·값 오류·중복·순서 오류가 있는
  종목-날짜는 넣지 않고 보고서 rejected에 남긴다(원본은 보존돼 있어 규칙을 고친 뒤 다시 처리할 수 있다).
- 불변: 이미 있는 행은 바꾸지 않는다. 종목-날짜 하나를 한 트랜잭션으로 넣는다.
  같은 키·같은 값 = 이미 있음, 같은 키·다른 값(또는 세션 구성이 다름) = 충돌(그 종목-날짜는 아무것도 쓰지 않고 DB 값·원본 값을 함께 기록),
  새 키 = 넣음. ON CONFLICT DO UPDATE를 쓰지 않는다. DB에도 UPDATE 거부 트리거가 있다. 다른 종목-날짜는 독립적으로 처리한다.
- 일정 상태: 그날 세션 일정이 data_verified가 아니면(예: 2026-11-19 announced) --load에는 --allow-unverified-schedule이 필요하다.
- 출처: 표의 source_file_sha256 + 보고서 days[](원본 파일 경로·SHA-256·종목·거래일·세션별 집계·집계 시각·일정·결과·거부 사유).
- 원본: <source>/raw/{ticker}.json. <source>/MANIFEST.sha256(52차 보존본)이 있으면 먼저 검증하고, 51차 보존본은 --manifest로 준다.
  원본은 읽기만 한다. 수집 시각은 fetch_log.json·session_audit_summary.json(원본 또는 source_meta/*/)에서 읽는다.
- 대상 DB: --database-url 필수, 기본값 없음. 허용 이름(ai_investment_session_*, captest_*)만 받고 개발·보존 DB는 거부한다
  (scripts.alembic_pending.check_target). 연결 뒤 current_database()를 다시 확인한다. 개발 DB는 승인 뒤 --allow-dev-db로만.
- 공식 종가 대조: 종가 단일가(없으면 정규장 마지막) 종가를 같은 날 stock_price.close_price와 비교해 보고만 한다(분류에 쓰지 않음).

종료 코드: 0 = 충돌·불일치 없음(거부된 종목-날짜는 warning), 1 = 충돌·검증 불일치·원본 검증 실패, 2 = 거부.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter, defaultdict
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from sqlalchemy import create_engine, text

from app import session_bars as sb
from app import session_schedule
from scripts import preserve_session_minute as psm
from scripts.alembic_pending import Refused, check_target

SOURCE_NAME = "naver_minute"
KST = sb.KST
MODES = ("validate", "aggregate", "dry_run", "load", "verify")
DATA_COLS = ("open_price", "high_price", "low_price", "close_price", "volume", "bar_count", "first_bar_at", "last_bar_at")
INSERT_SQL = text(
    "INSERT INTO stock_price_session (company_id, price_date, venue, session, open_price, high_price, low_price, close_price, volume,"
    " bar_count, first_bar_at, last_bar_at, source, source_file_sha256, rules_version, price_basis_date, collected_at)"
    " VALUES (:company_id, :price_date, :venue, :session, :open_price, :high_price, :low_price, :close_price, :volume, :bar_count,"
    " :first_bar_at, :last_bar_at, :source, :sha, :rules_version, :price_basis_date, :collected_at)")


def collection_info(source: Path) -> tuple[dict[str, datetime], datetime | None, str]:
    per, overall, basis = psm.collection_times(source)
    if basis != "file_mtime":
        return per, overall, basis
    meta = source / psm.META
    if meta.is_dir():  # 52차 보존본: source_meta/<원본 id>/fetch_log.json
        for sub in sorted(p for p in meta.iterdir() if p.is_dir()):
            p2, o2, b2 = psm.collection_times(sub)
            if b2 != "file_mtime":
                per = {**p2, **per}
                overall = overall or o2
                basis = f"{psm.META}/{sub.name}/{b2}"
    return per, overall, basis


def _row_values(agg: sb.SessionAgg) -> tuple:
    return (agg.open, agg.high, agg.low, agg.close, agg.volume, agg.bar_count, agg.first_bar_at, agg.last_bar_at)


def _jsonable(v):
    if isinstance(v, datetime):
        return v.astimezone(KST).isoformat()  # DB는 UTC로 돌려준다. 보고서는 KST로
    return str(v) if isinstance(v, (Decimal, date)) else v


def _official_match(close: Decimal, official) -> bool:
    off = Decimal(str(official))
    return abs(close - off) <= max(Decimal("0.5"), abs(off) * Decimal("0.0001"))


def schedules_for(targets: list[date]) -> dict[date, tuple[session_schedule.DaySchedule | None, str | None]]:
    return {d: session_schedule.resolve(d) for d in targets}


class Loader:
    def __init__(self, engine, source: Path, targets: list[date], tickers: set[str] | None, mode: str, database: str,
                 clock=None):
        assert mode in MODES
        self.engine, self.source, self.targets, self.tickers, self.mode = engine, source, targets, tickers, mode
        self.database = database
        self.clock = clock or (lambda: datetime.now(KST))
        self.counts: Counter = Counter()
        self.by_session: dict = defaultdict(Counter)  # (asset, session) -> {date: rows}
        self.days: list[dict] = []
        self.conflicts: list[dict] = []
        self.mismatches: list[dict] = []
        self.flags: Counter = Counter()
        self.official: Counter = Counter()
        self.official_examples: list[dict] = []

    # ---------- DB ----------
    def _connect_checked(self):
        conn = self.engine.connect()
        actual = conn.execute(text("SELECT current_database()")).scalar()
        if actual != self.database:  # 주소와 실제로 붙은 DB가 다르면 멈춘다(재확인)
            conn.close()
            raise Refused(f"연결된 DB({actual})가 지정한 DB({self.database})와 다릅니다")
        conn.rollback()  # 확인 조회가 시작한 트랜잭션을 닫는다(이어서 begin()·SET TRANSACTION을 쓰기 위해)
        return conn

    def companies(self, conn) -> dict[str, tuple[int, str]]:
        return {t: (i, a) for i, t, a in conn.execute(text("SELECT id, ticker, asset_type FROM company"))}

    def existing(self, conn, company_id: int, d: date) -> dict[str, tuple]:
        rows = conn.execute(text(f"SELECT session, {', '.join(DATA_COLS)} FROM stock_price_session "
                                 "WHERE company_id = :c AND price_date = :d AND venue = 'KRX'"), {"c": company_id, "d": d}).all()
        return {r[0]: tuple(r[1:]) for r in rows}

    # ---------- 실행 ----------
    def run(self) -> dict:
        per, overall, basis = collection_info(self.source)
        with self._connect_checked() as conn:
            conn.execute(text("SET TRANSACTION READ ONLY"))
            if self.mode in ("dry_run", "load", "verify") and \
                    conn.execute(text("SELECT to_regclass('public.stock_price_session')")).scalar() is None:
                raise Refused("stock_price_session 표가 없습니다(대기 마이그레이션 0f6d0ec52ac3 미적용)")
            comp = self.companies(conn)
        files = sorted((self.source / psm.RAW).glob("*.json"))
        for f in files:
            t = f.stem
            if self.tickers and t not in self.tickers:
                continue
            data = f.read_bytes()
            rec_base = {"ticker": t, "file": f"{psm.RAW}/{f.name}", "sha256": hashlib.sha256(data).hexdigest()}
            self.counts["files"] += 1
            if not psm.TICKER_RE.fullmatch(t) or t not in comp:
                self._reject({**rec_base, "date": None}, ["unknown_ticker"])
                continue
            company_id, asset = comp[t]
            rec_base["asset_type"] = asset
            try:
                by, problems, orphan = sb.parse_bars(sb.load_rows(data))
            except (UnicodeDecodeError, ValueError) as e:
                self._reject({**rec_base, "date": None}, [f"json_error: {str(e)[:80]}"])
                continue
            collected = per.get(t) or overall
            for d in self.targets:
                rec = {**rec_base, "date": d.isoformat(), "collected_at": _jsonable(collected) if collected else None}
                if d not in by and d not in problems:
                    rec.update(result="missing_date", reasons=["no_bars_for_date"])
                    self.counts["missing_date"] += 1
                    self.days.append(rec)
                    continue
                res = sb.classify_day(d, by.get(d, []), asset, collected, problems.get(d))
                if orphan:
                    res.reject("file_has_unplaceable_bars")
                rec["schedule"] = res.schedule
                rec["aggregated_at"] = self.clock().isoformat(timespec="seconds")
                if res.status != "ok":
                    rec["unclassified"] = res.unclassified[:20]
                    self._reject(rec, res.reasons)
                    continue
                rec["flags"] = res.flags
                rec["sessions"] = {s: dict(zip(DATA_COLS, map(_jsonable, _row_values(a)))) for s, a in res.sessions.items()}
                for fl in res.flags:
                    self.flags[(asset, fl)] += 1
                if self.mode == "validate":
                    rec["result"] = "valid"
                    self.counts["valid_days"] += 1
                elif self.mode == "aggregate":
                    rec["result"] = "aggregated"
                    self.counts["aggregated_days"] += 1
                    self.counts["aggregated_rows"] += len(res.sessions)
                    for s in res.sessions:
                        self.by_session[(asset, s)][d.isoformat()] += 1
                else:
                    self.handle_day(rec, company_id, asset, d, res, collected)
                self.days.append(rec)
        rejected = [r for r in self.days if r.get("result") == "rejected"]
        status = "failed" if self.conflicts or self.mismatches else ("warning" if rejected or self.counts["missing_date"] else "ok")
        return {
            "status": status, "mode": self.mode, "database": self.database, "source": str(self.source),
            "collected_basis": basis, "target_dates": [d.isoformat() for d in self.targets],
            "schedule_version": session_schedule.schedule().version, "rules_version": sb.RULES_VERSION,
            "counts": dict(self.counts), "rows_by_session": {f"{a}:{s}": dict(sorted(v.items())) for (a, s), v in sorted(self.by_session.items())},
            "flags": {f"{a}:{f}": n for (a, f), n in sorted(self.flags.items())},
            "official_close": dict(self.official), "official_close_examples": self.official_examples[:20],
            "rejected": [{k: r.get(k) for k in ("ticker", "date", "file", "reasons", "unclassified")} for r in rejected],
            "conflicts": self.conflicts, "mismatches": self.mismatches[:50], "days": self.days,
        }

    def _reject(self, rec: dict, reasons: list[str]) -> None:
        rec.update(result="rejected", reasons=reasons)
        self.counts["rejected_days"] += 1
        self.days.append(rec)

    def handle_day(self, rec: dict, company_id: int, asset: str, d: date, res: sb.DayResult, collected: datetime) -> None:
        new = {s: _row_values(a) for s, a in res.sessions.items()}
        with self._connect_checked() as conn:
            trans = conn.begin()
            try:
                if self.mode != "load":
                    conn.execute(text("SET TRANSACTION READ ONLY"))
                old = self.existing(conn, company_id, d)
                ref = res.sessions.get(sb.CLOSING_AUCTION) or res.sessions[sb.REGULAR]
                off = conn.execute(text("SELECT close_price FROM stock_price WHERE company_id = :c AND price_date = :d"),
                                   {"c": company_id, "d": d}).scalar()
                if off is None:
                    self.official["no_official_close"] += 1
                else:
                    ok = _official_match(ref.close, off)
                    self.official["match" if ok else "mismatch"] += 1
                    rec["official_close"] = {"session": ref.session, "minute_close": str(ref.close), "stock_price_close": str(off), "match": ok}
                    if not ok:
                        self.official_examples.append({"ticker": rec["ticker"], "date": d.isoformat(), **rec["official_close"]})
                if self.mode == "verify":
                    if old == new:
                        rec["result"] = "verified"
                        self.counts["verified_days"] += 1
                        self.counts["verified_rows"] += len(new)
                    else:
                        rec["result"] = "mismatch"
                        self.mismatches.append(self._diff(rec["ticker"], d, old, new, "db_vs_source"))
                    trans.rollback()
                    return
                if old:
                    if old == new:
                        rec["result"] = "already"
                        self.counts["already_days"] += 1
                        self.counts["already_rows"] += len(new)
                    else:
                        rec["result"] = "conflict"
                        self.conflicts.append(self._diff(rec["ticker"], d, old, new, "conflict"))
                    trans.rollback()
                    return
                if self.mode == "dry_run":
                    rec["result"] = "would_insert"
                    self.counts["would_insert_days"] += 1
                    self.counts["would_insert_rows"] += len(new)
                    trans.rollback()
                    return
                basis_date = collected.astimezone(KST).date()
                conn.execute(INSERT_SQL, [
                    {"company_id": company_id, "price_date": d, "venue": sb.VENUE_KRX, "session": s, "open_price": a.open,
                     "high_price": a.high, "low_price": a.low, "close_price": a.close, "volume": a.volume, "bar_count": a.bar_count,
                     "first_bar_at": a.first_bar_at, "last_bar_at": a.last_bar_at, "source": SOURCE_NAME, "sha": rec["sha256"],
                     "rules_version": sb.RULES_VERSION, "price_basis_date": basis_date, "collected_at": collected}
                    for s, a in res.sessions.items()])
                trans.commit()
            except Exception:
                trans.rollback()
                raise
        rec["result"] = "inserted"
        self.counts["inserted_days"] += 1
        self.counts["inserted_rows"] += len(new)
        for s in res.sessions:
            self.by_session[(asset, s)][d.isoformat()] += 1

    @staticmethod
    def _diff(t, d, old: dict, new: dict, kind: str) -> dict:
        sessions = sorted(set(old) | set(new))
        return {"kind": kind, "ticker": t, "date": d.isoformat(), "sessions": {
            s: {"db": None if s not in old else dict(zip(DATA_COLS, map(_jsonable, old[s]))),
                "source": None if s not in new else dict(zip(DATA_COLS, map(_jsonable, new[s])))}
            for s in sessions if old.get(s) != new.get(s)}}


def main(argv=None, clock=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--database-url", required=True, help="시험 DB 주소(기본값 없음). 127.0.0.1을 쓴다(localhost는 IPv6 대기 사례)")
    ap.add_argument("--source", required=True, type=Path)
    ap.add_argument("--manifest", type=Path, help="원본 폴더에 MANIFEST.sha256이 없을 때 쓸 체크섬 목록(51차 보존본)")
    ap.add_argument("--date", type=date.fromisoformat)
    ap.add_argument("--start", type=date.fromisoformat)
    ap.add_argument("--end", type=date.fromisoformat)
    ap.add_argument("--tickers", help="쉼표로 구분(없으면 전부)")
    m = ap.add_mutually_exclusive_group()
    m.add_argument("--validate-only", action="store_true", help="원본·세션 분류만 검증(쓰지 않음)")
    m.add_argument("--dry-run", action="store_true", help="넣을·이미 있는·충돌 행만 센다(READ ONLY)")
    m.add_argument("--load", action="store_true", help="적재(--confirm-database·--report 필요)")
    m.add_argument("--verify-only", action="store_true", help="DB 행을 원본에서 다시 계산한 값과 대조(READ ONLY)")
    ap.add_argument("--confirm-database", help="--load: 대상 DB 이름을 한 번 더 적는다")
    ap.add_argument("--allow-unverified-schedule", action="store_true", help="--load: data_verified가 아닌 일정(announced)의 날짜도 적재")
    ap.add_argument("--allow-dev-db", action="store_true", help="개발 DB(승인 뒤에만)")
    ap.add_argument("--report", type=Path, help="결과 JSON(원본 폴더 밖). --load에는 필수")
    args = ap.parse_args(argv)
    mode = ("validate" if args.validate_only else "dry_run" if args.dry_run else "load" if args.load
            else "verify" if args.verify_only else "aggregate")
    try:
        target = check_target(args.database_url, allow_dev_db=args.allow_dev_db)
        targets = psm.targets_from(args)
        if mode == "load":
            if args.confirm_database != target.database:
                raise Refused(f"--load에는 --confirm-database {target.database}가 필요합니다(지금: {args.confirm_database})")
            if not args.report:
                raise Refused("--load에는 출처 기록용 --report가 필요합니다")
        scheds = schedules_for(targets)
        for d, (ds, why) in scheds.items():  # DB에 붙기 전에 거부(분류 단계도 같은 이유로 거부한다)
            if ds is None:
                raise Refused(f"{d}: 세션 일정을 정할 수 없습니다({why})")
            if mode == "load" and ds.status != "data_verified" and not args.allow_unverified_schedule:
                raise Refused(f"{d}: 일정 상태가 {ds.status}입니다(실제 분봉 검증 전). 적재하려면 --allow-unverified-schedule")
        source = args.source.resolve()
        if not (source / psm.RAW).is_dir():
            raise Refused(f"원본 폴더에 raw/가 없습니다: {source}")
        if args.report and psm._inside(args.report.resolve(), source):
            raise Refused("--report는 원본 폴더 밖이어야 합니다")
        manifest = args.manifest or (source / psm.MANIFEST_SHA if (source / psm.MANIFEST_SHA).is_file() else None)
        if manifest is None:
            print("경고: 체크섬 목록이 없어 원본 무결성을 확인하지 않았습니다", file=sys.stderr)
            pre = None
        else:
            pre = psm.verify(source, manifest)
            if pre["status"] == "failed":
                print(f"원본 검증 실패: 불일치 {pre['mismatch'][:5]}, 없음 {pre['missing'][:5]}", file=sys.stderr)
                return 1
    except (Refused, psm.Refused) as e:
        print(f"거부: {e}", file=sys.stderr)
        return 2
    print(f"[{mode}] 대상 DB {target.database} @ {target.host}:{target.port}  원본 {source}  날짜 {targets[0]}~{targets[-1]}")
    for d, (ds, _) in scheds.items():
        if ds.kind != "default" or ds.status != "data_verified":
            print(f"  일정 {d}: {ds.kind}/{ds.status} {ds.label}")
    engine = create_engine(target.render_as_string(hide_password=False), connect_args={"connect_timeout": 10})
    try:
        out = Loader(engine, source, targets, set(args.tickers.split(",")) if args.tickers else None, mode, target.database,
                     clock).run()
    except Refused as e:
        print(f"거부: {e}", file=sys.stderr)
        return 2
    finally:
        engine.dispose()
    out["database_url"] = target.render_as_string(hide_password=True)
    out["source_manifest_check"] = None if pre is None else {k: pre[k] for k in ("status", "listed", "ok")}
    if args.report:
        args.report.write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    c = out["counts"]
    print(f"  파일 {c.get('files', 0)}, 검증 통과 {c.get('valid_days', 0)}일, 집계 {c.get('aggregated_days', 0)}일·{c.get('aggregated_rows', 0)}행, "
          f"넣음 {c.get('inserted_days', 0)}일·{c.get('inserted_rows', 0)}행, 이미 있음 {c.get('already_days', 0)}일·{c.get('already_rows', 0)}행, "
          f"넣을 예정 {c.get('would_insert_rows', 0)}행, 검증 일치 {c.get('verified_days', 0)}일·{c.get('verified_rows', 0)}행, "
          f"충돌 {len(out['conflicts'])}, 검증 불일치 {len(out['mismatches'])}, 거부 {c.get('rejected_days', 0)}, 그날 없음 {c.get('missing_date', 0)}")
    print(f"  공식 종가 대조 {out['official_close']}, 표시 {out['flags']}")
    for r in out["rejected"][:10]:
        print(f"  거부: {r['ticker']} {r['date']} {r['reasons']} {r.get('unclassified') or ''}")
    for cf in out["conflicts"][:5]:
        print(f"  충돌: {json.dumps(cf, ensure_ascii=False)[:400]}")
    print(f"결과: {out['status']}")
    return 1 if out["status"] == "failed" else 0


if __name__ == "__main__":
    sys.exit(main())
