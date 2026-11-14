"""보존한 분봉 원본을 세션별로 분류해 stock_price_session에 적재한다(2026-11-14, 53차). **승인 전에는 시험 DB에만.**

  python -m scripts.load_session_minute --database-url <시험 DB> --source <보존 폴더> --date 2026-11-13
  python -m scripts.load_session_minute ... --start 2026-11-06 --end 2026-11-13 [--tickers 005930,122630] [--dry-run]
  python -m scripts.load_session_minute ... --verify-only        # DB 행을 원본에서 다시 계산한 값과 대조(읽기만)

- 분류·집계: app/session_bars.py(규칙 버전 RULES_VERSION). 분류할 수 없는 봉이 있는 종목-날짜는 넣지 않고 보고서에 남긴다.
- 불변: 이미 있는 행은 바꾸지 않는다. 종목-날짜 하나를 한 트랜잭션으로 넣는다.
  같은 키·같은 값 = 이미 있음, 같은 키·다른 값(또는 세션 구성이 다름) = 충돌(아무것도 쓰지 않고 DB 값·원본 값을 함께 기록),
  새 키 = 넣음. ON CONFLICT DO UPDATE를 쓰지 않는다. DB에도 UPDATE 거부 트리거가 있다.
- 원본: <source>/raw/{ticker}.json. <source>/MANIFEST.sha256(52차 보존본)이 있으면 먼저 검증하고, 51차 보존본은 --manifest로 준다.
  원본은 읽기만 한다. 수집 시각은 fetch_log.json·session_audit_summary.json(원본 또는 source_meta/*/)에서 읽는다.
- 대상 DB: --database-url 필수. 개발 DB·보존 DB는 거부한다(scripts.alembic_pending.check_target). 개발 DB 적용은 승인 뒤
  --allow-dev-db로만 한다.
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
from scripts import preserve_session_minute as psm
from scripts.alembic_pending import Refused, check_target

SOURCE_NAME = "naver_minute"
KST = sb.KST
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


class Loader:
    def __init__(self, engine, source: Path, targets: list[date], tickers: set[str] | None, dry_run: bool, verify_only: bool):
        self.engine, self.source, self.targets = engine, source, targets
        self.tickers, self.dry_run, self.verify_only = tickers, dry_run, verify_only
        self.counts: Counter = Counter()
        self.by_session: dict = defaultdict(Counter)  # (asset, session) -> {date: rows}
        self.rejected: list[dict] = []
        self.conflicts: list[dict] = []
        self.mismatches: list[dict] = []
        self.flags: Counter = Counter()
        self.official: Counter = Counter()
        self.official_examples: list[dict] = []
        self.missing_dates: list[dict] = []
        self.files: dict[str, str] = {}

    def companies(self, conn) -> dict[str, tuple[int, str]]:
        return {t: (i, a) for i, t, a in conn.execute(text("SELECT id, ticker, asset_type FROM company"))}

    def existing(self, conn, company_id: int, d: date) -> dict[str, tuple]:
        rows = conn.execute(text(f"SELECT session, {', '.join(DATA_COLS)} FROM stock_price_session "
                                 "WHERE company_id = :c AND price_date = :d AND venue = 'KRX'"), {"c": company_id, "d": d}).all()
        return {r[0]: tuple(r[1:]) for r in rows}

    def official_close(self, conn, company_id: int, d: date):
        return conn.execute(text("SELECT close_price FROM stock_price WHERE company_id = :c AND price_date = :d"),
                            {"c": company_id, "d": d}).scalar()

    def run(self) -> dict:
        per, overall, basis = collection_info(self.source)
        with self.engine.connect() as conn:
            if conn.execute(text("SELECT to_regclass('public.stock_price_session')")).scalar() is None:
                raise Refused("stock_price_session 표가 없습니다(대기 마이그레이션 0f6d0ec52ac3 미적용)")
            comp = self.companies(conn)
        files = sorted((self.source / psm.RAW).glob("*.json"))
        for f in files:
            t = f.stem
            if self.tickers and t not in self.tickers:
                continue
            data = f.read_bytes()
            sha = hashlib.sha256(data).hexdigest()
            self.files[f.name] = sha
            if t not in comp:
                self.rejected.append({"ticker": t, "date": None, "reasons": ["unknown_ticker"]})
                continue
            company_id, asset = comp[t]
            try:
                by, problems, orphan = sb.parse_bars(sb.load_rows(data))
            except (UnicodeDecodeError, ValueError) as e:
                self.rejected.append({"ticker": t, "date": None, "reasons": [f"json_error: {str(e)[:80]}"]})
                continue
            collected = per.get(t) or overall
            for d in self.targets:
                if d not in by and d not in problems:
                    self.missing_dates.append({"ticker": t, "date": d.isoformat()})
                    continue
                res = sb.classify_day(d, by.get(d, []), asset, collected, problems.get(d))
                if orphan:
                    res.reject("file_has_unplaceable_bars")
                if res.status != "ok":
                    self.counts["rejected_days"] += 1
                    self.rejected.append({"ticker": t, "asset_type": asset, "date": d.isoformat(), "reasons": res.reasons,
                                          "unclassified": res.unclassified[:20]})
                    continue
                for fl in res.flags:
                    self.flags[(asset, fl)] += 1
                self.handle_day(t, company_id, asset, d, res, sha, collected)
        status = "failed" if self.conflicts or self.mismatches else ("warning" if self.rejected or self.missing_dates else "ok")
        return {
            "status": status, "mode": "verify-only" if self.verify_only else ("dry-run" if self.dry_run else "load"),
            "source": str(self.source), "collected_basis": basis, "target_dates": [d.isoformat() for d in self.targets],
            "files": len(self.files), "counts": dict(self.counts),
            "rows_by_session": {f"{a}:{s}": dict(sorted(v.items())) for (a, s), v in sorted(self.by_session.items())},
            "flags": {f"{a}:{f}": n for (a, f), n in sorted(self.flags.items())},
            "official_close": dict(self.official), "official_close_examples": self.official_examples[:20],
            "rejected": self.rejected, "missing_dates": self.missing_dates[:50], "conflicts": self.conflicts,
            "mismatches": self.mismatches[:50], "file_sha256": self.files,
        }

    def handle_day(self, t, company_id, asset, d, res: sb.DayResult, sha, collected) -> None:
        new = {s: _row_values(a) for s, a in res.sessions.items()}
        with self.engine.connect() as conn:
            trans = conn.begin()
            try:
                if self.dry_run or self.verify_only:
                    conn.execute(text("SET TRANSACTION READ ONLY"))
                old = self.existing(conn, company_id, d)
                ref = res.sessions.get(sb.CLOSING_AUCTION) or res.sessions[sb.REGULAR]
                off = self.official_close(conn, company_id, d)
                if off is None:
                    self.official["no_official_close"] += 1
                elif _official_match(ref.close, off):
                    self.official["match"] += 1
                else:
                    self.official["mismatch"] += 1
                    self.official_examples.append({"ticker": t, "date": d.isoformat(), "session": ref.session,
                                                   "minute_close": str(ref.close), "stock_price_close": str(off)})
                if self.verify_only:
                    if old == new:
                        self.counts["verified_days"] += 1
                        self.counts["verified_rows"] += len(new)
                    else:
                        self.mismatches.append(self._diff(t, d, old, new, "db_vs_source"))
                    trans.rollback()
                    return
                if old:
                    if old == new:
                        self.counts["already_days"] += 1
                        self.counts["already_rows"] += len(new)
                    else:
                        self.conflicts.append(self._diff(t, d, old, new, "conflict"))
                    trans.rollback()
                    return
                if self.dry_run:
                    self.counts["would_insert_days"] += 1
                    self.counts["would_insert_rows"] += len(new)
                    trans.rollback()
                    return
                basis_date = collected.astimezone(KST).date()
                conn.execute(INSERT_SQL, [
                    {"company_id": company_id, "price_date": d, "venue": sb.VENUE_KRX, "session": s, "open_price": a.open,
                     "high_price": a.high, "low_price": a.low, "close_price": a.close, "volume": a.volume, "bar_count": a.bar_count,
                     "first_bar_at": a.first_bar_at, "last_bar_at": a.last_bar_at, "source": SOURCE_NAME, "sha": sha,
                     "rules_version": sb.RULES_VERSION, "price_basis_date": basis_date, "collected_at": collected}
                    for s, a in res.sessions.items()])
                trans.commit()
            except Exception:
                trans.rollback()
                raise
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


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--database-url", required=True)
    ap.add_argument("--source", required=True, type=Path)
    ap.add_argument("--manifest", type=Path, help="원본 폴더에 MANIFEST.sha256이 없을 때 쓸 체크섬 목록(51차 보존본)")
    ap.add_argument("--date", type=date.fromisoformat)
    ap.add_argument("--start", type=date.fromisoformat)
    ap.add_argument("--end", type=date.fromisoformat)
    ap.add_argument("--tickers", help="쉼표로 구분(없으면 전부)")
    ap.add_argument("--dry-run", action="store_true", help="쓰지 않고 넣을·이미 있는·충돌 행만 센다(READ ONLY)")
    ap.add_argument("--verify-only", action="store_true", help="DB 행을 원본에서 다시 계산한 값과 대조(READ ONLY)")
    ap.add_argument("--allow-dev-db", action="store_true", help="개발 DB 적재(승인 뒤에만)")
    ap.add_argument("--report", type=Path, help="결과 JSON 파일(원본 폴더 밖)")
    args = ap.parse_args(argv)
    try:
        target = check_target(args.database_url, allow_dev_db=args.allow_dev_db)
        targets = psm.targets_from(args)
        for d in targets:  # DB에 붙기 전에 거부(분류 단계도 같은 이유로 거부한다)
            if sb.krx_calendar.is_trading_day(d) is not True:
                raise Refused(f"{d}는 KRX 거래일이 아니거나 달력으로 확인할 수 없습니다")
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
    except psm.Refused as e:
        print(f"거부: {e}", file=sys.stderr)
        return 2
    except Refused as e:
        print(f"거부: {e}", file=sys.stderr)
        return 2
    engine = create_engine(target.render_as_string(hide_password=False), connect_args={"connect_timeout": 10})
    try:
        out = Loader(engine, source, targets, set(args.tickers.split(",")) if args.tickers else None,
                     args.dry_run, args.verify_only).run()
    except Refused as e:
        print(f"거부: {e}", file=sys.stderr)
        return 2
    finally:
        engine.dispose()
    out["database"] = target.render_as_string(hide_password=True)
    out["source_manifest_check"] = None if pre is None else {k: pre[k] for k in ("status", "listed", "ok")}
    if args.report:
        args.report.write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    c = out["counts"]
    print(f"[{out['mode']}] {out['database']}  원본 {out['source']}  날짜 {out['target_dates'][0]}~{out['target_dates'][-1]}")
    print(f"  파일 {out['files']}, 넣음 {c.get('inserted_days', 0)}일·{c.get('inserted_rows', 0)}행, "
          f"이미 있음 {c.get('already_days', 0)}일·{c.get('already_rows', 0)}행, 넣을 예정 {c.get('would_insert_rows', 0)}행, "
          f"검증 일치 {c.get('verified_days', 0)}일·{c.get('verified_rows', 0)}행, 충돌 {len(out['conflicts'])}, "
          f"검증 불일치 {len(out['mismatches'])}, 거부 {len(out['rejected'])}, 그날 없음 {len(out['missing_dates'])}")
    print(f"  공식 종가 대조 {out['official_close']}, 표시 {out['flags']}")
    for r in out["rejected"][:10]:
        print(f"  거부: {r}")
    for cf in out["conflicts"][:5]:
        print(f"  충돌: {json.dumps(cf, ensure_ascii=False)[:400]}")
    print(f"결과: {out['status']}")
    return 1 if out["status"] == "failed" else 0


if __name__ == "__main__":
    sys.exit(main())
