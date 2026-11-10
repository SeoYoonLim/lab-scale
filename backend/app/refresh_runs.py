"""데이터 갱신 실행의 락·실행 기록·중단 복구·기록 보관(2026-11-07). scripts/refresh_data.py가 쓴다.

중복 실행 방지: Postgres 세션 advisory lock(pg_try_advisory_lock) 하나를 갱신 실행 전체가 잡는다. 락 연결은 풀을 쓰지 않는
전용 연결(NullPool, AUTOCOMMIT)이라
- 정상 종료: release()가 풀고 연결을 닫는다.
- 프로세스가 죽음(강제 종료, 정전): 연결이 끊기면 Postgres가 락을 풀어 준다. 파일 락처럼 남아서 다음 실행을 막지 않는다.
- 커넥션 풀에 락이 붙은 연결이 돌아가 API 요청이 우연히 락을 들고 있게 되는 일이 없다.
락은 API 서버의 읽기·쓰기를 막지 않는다(advisory lock은 행·표를 잠그지 않는다).

중단 복구: 락을 잡았다는 것은 살아 있는 다른 실행이 없다는 뜻이므로, 그때 남아 있는 running 실행은 aborted로 바꾼다.
수집은 모두 upsert/중복 검사라 같은 단계를 다시 실행해도 기존 행을 망가뜨리지 않는다(재실행: refresh_data --retry-failed).

기록 보관: data_refresh_log·data_refresh_run은 LOG_RETENTION_DAYS(180일)보다 오래된 행을 prune_logs로 지울 수 있다. 단계마다 마지막 성공·
마지막 실패 행은 오래돼도 남긴다(최근 실패 원인 분석용). 수집 데이터(주가·뉴스 등)는 지우지 않는다.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection
from sqlalchemy.pool import NullPool

# refresh_data 전체 실행용 advisory lock 키(임의의 고정 64비트 정수, 다른 기능과 겹치지 않게 이름에서 만든 값)
LOCK_KEY = 7_302_611_070_001
LOG_RETENTION_DAYS = 180
RUN_STATUSES = ("running", "success", "partial", "failed", "aborted")


class LockBusy(Exception):
    """다른 갱신 실행이 락을 잡고 있다."""


@dataclass
class RefreshLock:
    conn: Connection | None
    engine: object = None

    @property
    def held(self) -> bool:
        return self.conn is not None

    def release(self) -> None:
        if self.conn is None:
            return
        try:
            self.conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": LOCK_KEY})
        finally:
            self.conn.close()
            self.conn = None
            if self.engine is not None:
                self.engine.dispose()


def _lock_engine(url=None):
    if url is None:
        from app.db.session import engine

        url = engine.url
    # 앱 엔진(app/db/session)과 같은 connect_timeout. 없으면 연결 시도가 끝없이 매달릴 수 있다(Windows에서 localhost가 ::1을 먼저 시도)
    return create_engine(url, poolclass=NullPool, isolation_level="AUTOCOMMIT", connect_args={"connect_timeout": 5})


def acquire_lock(url=None) -> RefreshLock:
    """락을 잡는다. 이미 다른 실행이 잡고 있으면 LockBusy. DB에 못 붙으면 SQLAlchemy 예외가 그대로 올라간다."""
    eng = _lock_engine(url)
    conn = eng.connect()
    try:
        got = conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": LOCK_KEY}).scalar()
    except Exception:
        conn.close()
        eng.dispose()
        raise
    if not got:
        conn.close()
        eng.dispose()
        raise LockBusy("다른 데이터 갱신이 실행 중입니다")
    return RefreshLock(conn, eng)


def lock_is_held_elsewhere(db) -> bool:
    """다른 세션이 갱신 락을 잡고 있는지(상태 조회용, 락을 잡지 않는다)."""
    hi, lo = divmod(LOCK_KEY, 2 ** 32)
    return bool(db.execute(text(
        "SELECT EXISTS (SELECT 1 FROM pg_locks WHERE locktype = 'advisory' AND granted "
        "AND classid = :hi AND objid = :lo AND objsubid = 1)"), {"hi": hi, "lo": lo}).scalar())


# ---------- 실행 기록 ----------


def recover_aborted(db, now: datetime | None = None) -> int:
    """락을 잡은 뒤에 호출한다: 남아 있는 running 실행은 끝나지 못한 실행이다 -> aborted."""
    now = now or datetime.now(timezone.utc)
    n = db.execute(text(
        "UPDATE data_refresh_run SET status = 'aborted', finished_at = COALESCE(heartbeat_at, started_at), "
        "note = '프로세스가 끝나지 못함(다음 실행이 정리)' WHERE status = 'running'")).rowcount
    db.commit()
    return n or 0


def start_run(db, steps: list[str], trigger: str = "manual", now: datetime | None = None) -> int:
    now = now or datetime.now(timezone.utc)
    rid = db.execute(text(
        "INSERT INTO data_refresh_run (started_at, heartbeat_at, status, trigger, steps, pid) "
        "VALUES (:t, :t, 'running', :trigger, :steps, :pid) RETURNING id"),
        {"t": now, "trigger": trigger[:20], "steps": ",".join(steps)[:200], "pid": os.getpid()}).scalar()
    db.commit()
    return int(rid)


def heartbeat(db, run_id: int, now: datetime | None = None) -> None:
    db.execute(text("UPDATE data_refresh_run SET heartbeat_at = :t WHERE id = :id"),
               {"t": now or datetime.now(timezone.utc), "id": run_id})
    db.commit()


def finish_run(db, run_id: int, status: str, exit_code: int, note: str | None = None, now: datetime | None = None) -> None:
    if status not in RUN_STATUSES:
        raise ValueError(status)
    now = now or datetime.now(timezone.utc)
    db.execute(text("UPDATE data_refresh_run SET status = :s, exit_code = :c, finished_at = :t, heartbeat_at = :t, "
                    "note = :n WHERE id = :id"),
               {"s": status, "c": exit_code, "t": now, "n": (note or None) and note[:200], "id": run_id})
    db.commit()


def run_status(step_statuses: list[str]) -> str:
    """단계 결과로 실행 상태: 실행한 단계 중 실패가 있으면 failed(전부 실패) 또는 partial(일부 실패), partial 단계가 있으면 partial."""
    ran = [s for s in step_statuses if s != "skipped"]
    if not ran:
        return "success"
    if all(s == "failed" for s in ran):
        return "failed"
    if any(s in ("failed", "partial") for s in ran):
        return "partial"
    return "success"


def set_step_quality(db, run_id: int, step: str, quality_status: str, warning_count: int) -> None:
    db.execute(text("UPDATE data_refresh_log SET quality_status = :q, warning_count = :w WHERE run_id = :r AND step = :s"),
               {"q": quality_status, "w": warning_count, "r": run_id, "s": step})
    db.commit()


# ---------- 단계 이력 조회 ----------


def step_history(db, steps: list[str], recent: int = 5) -> dict[str, dict]:
    """단계마다 마지막 시도(skipped 제외), 마지막 성공, 마지막 실패(failed·partial), 최근 시도 몇 건."""
    out: dict[str, dict] = {s: {"last_attempt": None, "last_success": None, "last_failure": None, "recent": []} for s in steps}
    rows = db.execute(text(
        "SELECT id, step, status, started_at, finished_at, rows_added, latest_date, error_type, run_id, duration_ms, "
        "retry_count, quality_status, warning_count, error_code FROM data_refresh_log "
        "WHERE step = ANY(:steps) AND status <> 'skipped' ORDER BY id DESC LIMIT 2000"), {"steps": steps}).mappings().all()
    for r in rows:
        h = out[r["step"]]
        d = dict(r)
        if h["last_attempt"] is None:
            h["last_attempt"] = d
        if len(h["recent"]) < recent:
            h["recent"].append(d)
        if r["status"] == "success" and h["last_success"] is None:
            h["last_success"] = d
        if r["status"] in ("failed", "partial") and h["last_failure"] is None:
            h["last_failure"] = d
    return out


def failed_steps(db, steps: list[str]) -> list[str]:
    """마지막 시도가 failed·partial인 단계(--retry-failed 대상). 성공한 단계는 다시 수집하지 않는다."""
    hist = step_history(db, steps, recent=1)
    return [s for s in steps if (hist[s]["last_attempt"] or {}).get("status") in ("failed", "partial")]


def current_run(db) -> dict | None:
    row = db.execute(text("SELECT id, started_at, heartbeat_at, steps, pid FROM data_refresh_run WHERE status = 'running' "
                          "ORDER BY id DESC LIMIT 1")).mappings().first()
    return dict(row) if row else None


def recent_runs(db, limit: int = 10) -> list[dict]:
    return [dict(r) for r in db.execute(text(
        "SELECT id, started_at, finished_at, status, trigger, steps, exit_code, note FROM data_refresh_run "
        "ORDER BY id DESC LIMIT :n"), {"n": limit}).mappings().all()]


# ---------- 보관 ----------


def prune_logs(db, keep_days: int = LOG_RETENTION_DAYS, now: datetime | None = None, dry_run: bool = False) -> dict:
    """keep_days보다 오래된 단계 기록·실행 기록을 지운다. 단계마다 마지막 성공·마지막 실패 행과, 실행 중인 실행은 남긴다."""
    if keep_days < 30:
        raise ValueError("keep_days는 30일 이상이어야 합니다(최근 실패 분석에 필요한 기록을 남긴다)")
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=keep_days)
    keep_sql = ("SELECT max(id) FROM data_refresh_log WHERE status = 'success' GROUP BY step "
                "UNION SELECT max(id) FROM data_refresh_log WHERE status IN ('failed', 'partial') GROUP BY step")
    logs = db.execute(text(f"SELECT count(*) FROM data_refresh_log WHERE started_at < :c AND id NOT IN ({keep_sql})"),
                      {"c": cutoff}).scalar()
    runs_sql = ("FROM data_refresh_run r WHERE r.started_at < :c AND r.status <> 'running' AND NOT EXISTS "
                f"(SELECT 1 FROM data_refresh_log l WHERE l.run_id = r.id AND (l.started_at >= :c OR l.id IN ({keep_sql})))")
    runs = db.execute(text(f"SELECT count(*) {runs_sql}"), {"c": cutoff}).scalar()
    if not dry_run:
        db.execute(text(f"DELETE FROM data_refresh_log WHERE started_at < :c AND id NOT IN ({keep_sql})"), {"c": cutoff})
        db.execute(text(f"DELETE {runs_sql}"), {"c": cutoff})
        db.commit()
    return {"cutoff": cutoff.isoformat(), "logs": int(logs or 0), "runs": int(runs or 0), "dry_run": dry_run}
