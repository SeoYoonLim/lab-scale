"""클라우드 수집 패키지 -> 로컬 ai_investment 동기화(2026-11-23).

## 흐름
1. `git fetch origin market-data`(집 PC가 이미 쓰는 SSH 자격 증명. 새 토큰 없음) -> refs/remotes/origin/market-data
2. 아직 적용하지 않은 manifest를 날짜·실행 순서로 읽는다(적용 기록: ~/.lab-scale/market_data_sync_state.json)
3. 구역마다 체크섬·키·스키마·기준일을 확인한다(app.market_package.read_section). 틀린 구역은 그 구역만 실패로 남기고 넘어간다.
4. 기존 저장 규칙으로 넣는다. **없는 행만 넣는다**(ON CONFLICT DO NOTHING). 이미 있는 날짜는 값이 달라도 바꾸지 않고 수만 센다.
   - 주식·ETF·지수·환율: 저장 전 검증(app.ingest_validation)을 그대로 거친다. 격리 대상은 넣지 않는다.
   - 뉴스: app.news_recovery.store_news(relevant_only=True, 63차 정책) - 금융 외 기사·ETF 이름 확인·(종목, URL) 중복·검증
   - 공시: app.collectors.store_disclosure_items - 회사 명의만·접수번호(URL) 중복·검증
5. refresh_data와 같은 advisory lock을 잡는다(동시에 쓰지 않는다). 기록은 data_refresh_log(step=cloud_sync, 스키마 변경 없음).

## 언제 도는가(app/main.py, app/api/daily_briefing.py)
- 백엔드 시작 직후(백그라운드 스레드, 서버 기동을 막지 않음), 그 뒤 SYNC_INTERVAL_MIN마다
- 일일 브리핑을 만들기 직전(마지막 확인이 BRIEFING_RECHECK_MIN보다 오래됐으면, 최대 BRIEFING_WAIT_S초만 기다림)
- 수동: python -m scripts.sync_market_data [--dry-run] [--status]
- 끄기: 환경변수 MARKET_DATA_SYNC=0. 키(MARKET_DATA_KEY 또는 ~/.lab-scale/market_data_key)가 없으면 돌지 않는다.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

from app import market_package as P

logger = logging.getLogger(__name__)
KST_NAME = "Asia/Seoul"
BRANCH = "market-data"
REMOTE_REF = f"refs/remotes/origin/{BRANCH}"
STATE_FILE = Path.home() / ".lab-scale" / "market_data_sync_state.json"
SYNC_INTERVAL_MIN = 30
BRIEFING_RECHECK_MIN = 10
BRIEFING_WAIT_S = 25
GIT_TIMEOUT_S = 60
STEP = "cloud_sync"
PRICE_SECTIONS = ("stocks", "etfs")
ALL_SECTIONS = P.SECTIONS


def _kst():
    from zoneinfo import ZoneInfo

    return ZoneInfo(KST_NAME)


# ---------- 원천(git 브랜치 또는 폴더) ----------


class GitSource:
    def __init__(self, repo_dir: Path | None = None):
        self.repo = Path(repo_dir or Path(__file__).resolve().parents[2])

    def _git(self, *args: str, binary: bool = False, timeout: int = GIT_TIMEOUT_S):
        env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
        env.setdefault("GIT_SSH_COMMAND", "ssh -o BatchMode=yes -o ConnectTimeout=15")
        r = subprocess.run(["git", "-C", str(self.repo), *args], capture_output=True, timeout=timeout, env=env)
        if r.returncode != 0:
            raise RuntimeError(f"git {args[0]} 실패: {r.stderr.decode('utf-8', 'replace').strip()[:300]}")
        return r.stdout if binary else r.stdout.decode("utf-8")

    def fetch(self) -> None:
        self._git("fetch", "--quiet", "origin", f"+refs/heads/{BRANCH}:{REMOTE_REF}")

    def list_manifests(self) -> list[str]:
        try:
            out = self._git("ls-tree", "-r", "--name-only", REMOTE_REF, "packages/")
        except RuntimeError:
            return []
        return sorted(p for p in out.splitlines() if p.endswith("/manifest.json"))

    def read(self, path: str) -> bytes:
        return self._git("show", f"{REMOTE_REF}:{path}", binary=True)


class DirSource:
    """market-data 브랜치와 같은 구조의 폴더(시험·수동 적용용)."""

    def __init__(self, root: Path):
        self.root = Path(root)

    def fetch(self) -> None:
        return None

    def list_manifests(self) -> list[str]:
        return sorted(p.relative_to(self.root).as_posix() for p in self.root.glob("packages/*/*/manifest.json"))

    def read(self, path: str) -> bytes:
        return (self.root / path).read_bytes()


# ---------- 적용 기록 ----------


def load_state(path: Path = STATE_FILE) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"applied": {}}


def save_state(state: dict, path: Path = STATE_FILE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


# ---------- 구역 적용(DB) ----------


@dataclass
class SectionResult:
    status: str = "ok"  # ok / failed / skipped
    rows: int = 0
    inserted: int = 0
    existing_same: int = 0
    existing_different: int = 0  # 이미 있는 날짜인데 값이 다르다(덮어쓰지 않음)
    quarantined: int = 0
    unknown: int = 0  # DB에 없는 종목(새로 만들지 않는다)
    error: str | None = None
    latest_date: str | None = None
    notes: list[str] = field(default_factory=list)


def _d(s: str) -> date:
    return date.fromisoformat(s)


def _close(a, b) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return abs(float(a) - float(b)) < 0.005


def apply_prices(db, rows: list[dict], dry_run: bool, today: date) -> SectionResult:
    from sqlalchemy import select
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from app import ingest_validation as V, quarantine
    from app.models import Company, StockPrice

    res = SectionResult(rows=len(rows))
    by_ticker: dict[str, list[dict]] = {}
    for r in rows:
        by_ticker.setdefault(r["ticker"], []).append(r)
    companies = {c.ticker: c for c in db.execute(select(Company).where(Company.ticker.in_(list(by_ticker)))).scalars()}
    for ticker, rs in by_ticker.items():
        c = companies.get(ticker)
        if c is None or c.asset_type != rs[0]["asset_type"]:
            res.unknown += len(rs)
            continue
        rs = sorted(rs, key=lambda r: r["date"])
        existing = {row.price_date: row for row in db.execute(select(StockPrice).where(
            StockPrice.company_id == c.id, StockPrice.price_date >= _d(rs[0]["date"]))).scalars()}
        new = []
        for r in rs:
            e = existing.get(_d(r["date"]))
            if e is None:
                new.append(r)
            elif _close(e.close_price, r["close"]) and (e.volume is None or r["volume"] is None or int(e.volume) == int(r["volume"])):
                res.existing_same += 1
            else:
                res.existing_different += 1
        if not new:
            continue
        recs = [{"price_date": _d(r["date"]), "open_price": r["open"], "high_price": r["high"], "low_price": r["low"],
                 "close_price": r["close"], "volume": r["volume"], "change_pct": r["change_pct"]} for r in new]
        prev = db.execute(select(StockPrice.close_price).where(StockPrice.company_id == c.id, StockPrice.price_date < recs[0]["price_date"])
                          .order_by(StockPrice.price_date.desc()).limit(1)).scalar()
        checked = V.check_prices(ticker, recs, {d: float(e.close_price) for d, e in existing.items()},
                                 float(prev) if prev is not None else None, today)
        keep, bad = V.split(checked)
        res.quarantined += len(bad)
        recs = [k.record for k in keep]
        if dry_run:
            res.inserted += len(recs)  # dry-run: 넣을 예정 수
            continue
        if not recs:
            continue
        quarantine.record("stock_price", checked, {"ticker": ticker, "via": STEP})
        stmt = pg_insert(StockPrice).values([{**x, "company_id": c.id} for x in recs]).on_conflict_do_nothing(
            constraint="uq_stock_price_company_date").returning(StockPrice.id)
        res.inserted += len(db.execute(stmt).all())  # rowcount는 묶음 INSERT에서 -1일 수 있어 RETURNING으로 센다
    if not dry_run:
        db.commit()
    res.latest_date = max((r["date"] for r in rows), default=None)
    return res


def _apply_series(db, rows: list[dict], dry_run: bool, today: date, kind: str) -> SectionResult:
    """kind: index(market_index, code) / fx(exchange_rate, pair)."""
    from sqlalchemy import select
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from app import ingest_validation as V, quarantine
    from app.models import ExchangeRate, MarketIndex

    Model, key_col, field_name, constraint, check = (
        (MarketIndex, MarketIndex.index_code, "code", "uq_market_index_code_date", V.check_index) if kind == "index"
        else (ExchangeRate, ExchangeRate.pair_code, "pair", "uq_exchange_rate_pair_date", V.check_fx))
    res = SectionResult(rows=len(rows))
    groups: dict[str, list[dict]] = {}
    for r in rows:
        groups.setdefault(r[field_name], []).append(r)
    for code, rs in groups.items():
        rs = sorted(rs, key=lambda r: r["date"])
        existing = {row.price_date: row for row in db.execute(select(Model).where(key_col == code, Model.price_date >= _d(rs[0]["date"]))).scalars()}
        new = []
        for r in rs:
            e = existing.get(_d(r["date"]))
            if e is None:
                new.append(r)
            elif _close(e.close_price, r["close"]):
                res.existing_same += 1
            else:
                res.existing_different += 1
        if not new:
            continue
        recs = []
        for r in new:
            # 직전 종가 = 이 날짜 앞의 가장 최근 값(DB 행과 이번에 넣을 행 중 더 늦은 쪽)
            row = db.execute(select(Model.price_date, Model.close_price).where(key_col == code, Model.price_date < _d(r["date"]))
                             .order_by(Model.price_date.desc()).limit(1)).first()
            prev = float(row[1]) if row else None
            if recs and (row is None or recs[-1]["price_date"] > row[0]):
                prev = float(recs[-1]["close_price"])
            chg = r.get("change_pct")
            if kind == "fx" or chg is None:  # 환율 등락률은 저장된 직전 종가로 다시 계산한다(구간 첫 행이 None이 되지 않게)
                chg = round((float(r["close"]) / float(prev) - 1) * 100, 2) if prev else chg
            recs.append({"price_date": _d(r["date"]), "close_price": r["close"], "change_pct": chg})
        prev0 = db.execute(select(Model.close_price).where(key_col == code, Model.price_date < recs[0]["price_date"])
                           .order_by(Model.price_date.desc()).limit(1)).scalar()
        checked = check(code, recs, {d: float(e.close_price) for d, e in existing.items()}, float(prev0) if prev0 is not None else None, today)
        keep, bad = V.split(checked)
        res.quarantined += len(bad)
        recs = [k.record for k in keep]
        if dry_run:
            res.inserted += len(recs)
            continue
        if not recs:
            continue
        quarantine.record("market_index" if kind == "index" else "exchange_rate", checked,
                          {("index_code" if kind == "index" else "pair_code"): code, "via": STEP})
        col = "index_code" if kind == "index" else "pair_code"
        stmt = pg_insert(Model).values([{**x, col: code} for x in recs]).on_conflict_do_nothing(constraint=constraint).returning(Model.id)
        res.inserted += len(db.execute(stmt).all())
    if not dry_run:
        db.commit()
    res.latest_date = max((r["date"] for r in rows), default=None)
    return res


def apply_news(db, rows: list[dict], dry_run: bool) -> SectionResult:
    from sqlalchemy import select

    from app import news_recovery as R
    from app.models import Company

    res = SectionResult(rows=len(rows))
    by_ticker: dict[str, list[dict]] = {}
    for r in rows:
        by_ticker.setdefault(r["ticker"], []).append({k: r.get(k) for k in ("title", "originallink", "link", "description", "pubDate")})
    companies = {c.ticker: c for c in db.execute(select(Company).where(Company.ticker.in_(list(by_ticker)))).scalars()}
    listed = [n for (n,) in db.execute(select(Company.name))]
    etf_names = [n for (n,) in db.execute(select(Company.name).where(Company.asset_type == "etf"))]
    for ticker, items in by_ticker.items():
        c = companies.get(ticker)
        if c is None:
            res.unknown += len(items)
            continue
        s = R.store_news(db, c, items, dry_run=dry_run, listed_names=listed, etf_names=[n for n in etf_names if n != c.name],
                         relevant_only=True)
        res.inserted += s.new
        res.existing_same += s.existing
        res.quarantined += s.quarantined
    res.latest_date = max((r["date"] for r in rows if r.get("date")), default=None)
    return res


def apply_disclosures(db, rows: list[dict], dry_run: bool) -> SectionResult:
    from sqlalchemy import select

    from app import collectors as C
    from app.models import Company

    res = SectionResult(rows=len(rows))
    by_ticker: dict[str, list[dict]] = {}
    for r in rows:
        by_ticker.setdefault(r["ticker"], []).append({k: v for k, v in r.items() if k not in ("date", "ticker")})
    companies = {c.ticker: c for c in db.execute(select(Company).where(Company.ticker.in_(list(by_ticker)))).scalars()}
    for ticker, items in by_ticker.items():
        c = companies.get(ticker)
        if c is None:
            res.unknown += len(items)
            continue
        r = C.store_disclosure_items(db, c, items, dry_run=dry_run, via=STEP)
        if not r.ok:
            res.status, res.error = "failed", r.error
            continue
        res.inserted += r.saved
        res.quarantined += r.quarantined
        res.existing_same += max(0, r.fetched - r.skipped - r.saved - r.quarantined)
    res.latest_date = max((r["date"] for r in rows if r.get("date")), default=None)
    return res


def apply_section(db, name: str, rows: list[dict], dry_run: bool, today: date) -> SectionResult:
    if name in PRICE_SECTIONS:
        return apply_prices(db, rows, dry_run, today)
    if name == "indices":
        return _apply_series(db, rows, dry_run, today, "index")
    if name == "fx":
        return _apply_series(db, rows, dry_run, today, "fx")
    if name == "news":
        return apply_news(db, rows, dry_run)
    if name == "disclosures":
        return apply_disclosures(db, rows, dry_run)
    raise ValueError(name)


def apply_manifest(db, source, path: str, key: bytes, dry_run: bool = False, today: date | None = None) -> dict:
    """manifest 하나를 적용한다. 구역마다 따로 성공·실패한다(한 구역 실패가 다른 구역을 막지 않는다)."""
    today = today or datetime.now(_kst()).date()
    manifest = json.loads(source.read(path))
    if manifest.get("schema_version") != P.SCHEMA_VERSION:
        return {"manifest": path, "error": f"스키마 버전이 다르다: {manifest.get('schema_version')}", "sections": {}}
    base = path.rsplit("/", 1)[0]
    out: dict[str, dict] = {}
    for name in ALL_SECTIONS:
        entry = (manifest.get("sections") or {}).get(name) or {}
        if not entry.get("file"):
            out[name] = {"status": "skipped", "source_status": entry.get("status"), "note": entry.get("note")}
            continue
        try:
            rows = P.read_section(manifest, name, source.read(f"{base}/{entry['file']}"), key)
        except Exception as e:  # noqa: BLE001 - 체크섬·인증·형식 오류: 그 구역만 버린다
            out[name] = {"status": "failed", "error": str(e)[:300]}
            continue
        try:
            r = apply_section(db, name, rows, dry_run, today)
        except Exception as e:  # noqa: BLE001
            db.rollback()
            out[name] = {"status": "failed", "error": f"{type(e).__name__}: {str(e)[:300]}"}
            continue
        out[name] = {**r.__dict__, "source_status": entry.get("status")}
    return {"manifest": path, "data_date": manifest.get("data_date"), "generated_at_kst": manifest.get("generated_at_kst"), "sections": out}


# ---------- 전체 동기화 ----------


_lock = threading.Lock()
_last = {"checked_at": 0.0, "result": None}


def enabled(env: dict | None = None) -> bool:
    env = os.environ if env is None else env
    if env.get("MARKET_DATA_SYNC", "1") == "0" or "pytest" in sys.modules:
        return False
    try:
        return P.load_key(env) is not None
    except P.PackageError:
        return False


def sync(source=None, key: bytes | None = None, dry_run: bool = False, reapply: bool = False, state_path: Path = STATE_FILE,
         session_factory=None, use_lock: bool = True, fetch: bool = True) -> dict:
    """새 패키지를 받아 적용한다. 예외를 내지 않고 결과 dict를 돌려준다(서버 훅에서 부르므로)."""
    started = datetime.now(_kst())
    result: dict = {"started_at": started.isoformat(), "dry_run": dry_run, "applied": [], "skipped_known": 0}
    try:
        key = key or P.load_key()
        if key is None:
            return {**result, "status": "disabled", "error": "MARKET_DATA_KEY 없음"}
        source = source or GitSource()
        if fetch:
            source.fetch()
        manifests = source.list_manifests()
        state = load_state(state_path)
        todo = [m for m in manifests if reapply or m not in state.get("applied", {})]
        result["skipped_known"] = len(manifests) - len(todo)
        result["available"] = len(manifests)
        if not todo:
            return {**result, "status": "up_to_date"}
        if session_factory is None:
            from app.db.session import SessionLocal as session_factory  # noqa: N813
        lock = None
        if use_lock and not dry_run:
            from app import refresh_runs

            try:
                lock = refresh_runs.acquire_lock()
            except refresh_runs.LockBusy:
                return {**result, "status": "locked", "error": "다른 갱신 작업이 실행 중(advisory lock)"}
        try:
            db = session_factory()
            try:
                for m in todo:
                    r = apply_manifest(db, source, m, key, dry_run=dry_run)
                    result["applied"].append(r)
                    if not dry_run and not r.get("error") and all(s.get("status") != "failed" for s in r["sections"].values()):
                        state.setdefault("applied", {})[m] = {"at": datetime.now(_kst()).isoformat(),
                                                              "inserted": {k: v.get("inserted", 0) for k, v in r["sections"].items()}}
                if not dry_run:
                    save_state(state, state_path)
                    _record(db, started, result)
            finally:
                db.close()
        finally:
            if lock is not None:
                lock.release()
        failed = [m["manifest"] for m in result["applied"] if m.get("error") or any(s.get("status") == "failed" for s in m["sections"].values())]
        return {**result, "status": "partial" if failed else "ok", "failed_manifests": failed}
    except Exception as e:  # noqa: BLE001
        logger.warning("시장 데이터 동기화 실패: %s", str(e)[:300])
        return {**result, "status": "error", "error": f"{type(e).__name__}: {str(e)[:300]}"}


def _record(db, started: datetime, result: dict) -> None:
    from app.models import DataRefreshLog

    inserted = sum(s.get("inserted", 0) for m in result["applied"] for s in m["sections"].values())
    latest = max((s.get("latest_date") for m in result["applied"] for k, s in m["sections"].items()
                  if k in ("indices",) and s.get("latest_date")), default=None)
    failed = any(m.get("error") or any(s.get("status") == "failed" for s in m["sections"].values()) for m in result["applied"])
    now = datetime.now(_kst())
    db.add(DataRefreshLog(step=STEP, status="partial" if failed else "success", started_at=started, finished_at=now,
                          rows_added=inserted, latest_date=date.fromisoformat(latest) if latest else None,
                          duration_ms=round((now - started).total_seconds() * 1000),
                          error_code="section_failed" if failed else None))
    db.commit()


def sync_now(reason: str) -> dict:
    """서버 훅용: 동시에 하나만. 결과는 로그로만 남긴다."""
    if not _lock.acquire(blocking=False):
        return {"status": "busy"}
    try:
        r = sync()
        _last.update(checked_at=time.monotonic(), result=r)
        ins = sum(s.get("inserted", 0) for m in r.get("applied", []) for s in m["sections"].values())
        logger.info("시장 데이터 동기화(%s): %s, 새 패키지 %d개, 넣은 행 %d", reason, r.get("status"), len(r.get("applied", [])), ins)
        return r
    finally:
        _lock.release()


def sync_before_briefing(wait_s: float = BRIEFING_WAIT_S) -> dict | None:
    """브리핑 직전: 마지막 확인이 BRIEFING_RECHECK_MIN보다 오래됐으면 동기화를 시작하고 wait_s초까지 기다린다."""
    if not enabled():
        return None
    if _last["checked_at"] and time.monotonic() - _last["checked_at"] < BRIEFING_RECHECK_MIN * 60:
        return _last["result"]
    t = threading.Thread(target=sync_now, args=("briefing",), daemon=True)
    t.start()
    t.join(wait_s)
    return _last["result"]


def start_background(interval_min: float = SYNC_INTERVAL_MIN) -> threading.Thread | None:
    """백엔드 시작 시: 바로 한 번, 그 뒤 interval_min마다. 키가 없거나 MARKET_DATA_SYNC=0이면 시작하지 않는다."""
    if not enabled():
        logger.info("시장 데이터 동기화 꺼짐(키 없음 또는 MARKET_DATA_SYNC=0)")
        return None

    def loop():
        while True:
            try:
                sync_now("startup" if not _last["checked_at"] else "interval")
            except Exception:  # noqa: BLE001
                logger.exception("시장 데이터 동기화 스레드 오류")
            time.sleep(interval_min * 60)

    t = threading.Thread(target=loop, name="market-data-sync", daemon=True)
    t.start()
    return t
