"""데이터 갱신 운영 기능의 DB 통합 테스트(2026-11-07): advisory lock, 실행 기록·중단 복구·보관, 품질 검사·상태 보고서,
락을 잡은 동안 API 동시 조회, 재수집 멱등성. 외부 API는 부르지 않는다(수집 함수 입력은 합성 데이터로 바꿔 끼운다).

dev DB와 CI의 빈 DB(시드만) 모두에서 돈다(수집 데이터가 없어도 되는 검사만 한다). 테스트가 만든 행은 테스트가 지운다:
data_refresh_run(note='pytest'), data_refresh_log(그 run_id 또는 step 'zz_pytest'), exchange_rate(pair_code 'TST/KRW').
"""

from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest
from sqlalchemy import text

from app import data_monitor as DM
from app import refresh_runs as RR

pytestmark = pytest.mark.integration

TEST_PAIR = "TST/KRW"


@pytest.fixture
def cleanup(dev_db):
    run_ids: list[int] = []
    yield run_ids
    dev_db.rollback()
    if run_ids:
        dev_db.execute(text("DELETE FROM data_refresh_log WHERE run_id = ANY(:ids)"), {"ids": run_ids})
        dev_db.execute(text("DELETE FROM data_refresh_run WHERE id = ANY(:ids)"), {"ids": run_ids})
    dev_db.execute(text("DELETE FROM data_refresh_log WHERE step = 'zz_pytest'"))
    dev_db.execute(text("DELETE FROM data_refresh_run WHERE note = 'pytest'"))
    dev_db.execute(text("DELETE FROM exchange_rate WHERE pair_code = :p"), {"p": TEST_PAIR})
    dev_db.commit()


def _no_other_refresh(dev_db):
    if RR.lock_is_held_elsewhere(dev_db) or RR.current_run(dev_db):
        pytest.skip("실제 데이터 갱신이 실행 중이라 락 테스트를 건너뜀")


# ---------- 락 ----------

def test_lock_acquire_busy_release(dev_db):
    _no_other_refresh(dev_db)
    lock = RR.acquire_lock()
    try:
        assert RR.lock_is_held_elsewhere(dev_db)
        with pytest.raises(RR.LockBusy):
            RR.acquire_lock()  # 같은 작업의 동시 실행 방지
    finally:
        lock.release()
    assert not RR.lock_is_held_elsewhere(dev_db)
    RR.acquire_lock().release()  # 풀린 뒤 다시 잡힌다


def test_lock_is_released_when_process_dies(dev_db):
    """프로세스가 죽으면 연결이 끊기고 Postgres가 락을 푼다(락이 영구히 남지 않는다). 연결을 unlock 없이 닫아 흉내 낸다."""
    _no_other_refresh(dev_db)
    lock = RR.acquire_lock()
    lock.conn.invalidate()  # unlock을 보내지 않고 연결을 버린다
    lock.conn.close()
    lock.engine.dispose()
    lock.conn = None
    again = RR.acquire_lock()
    again.release()


def test_api_and_db_work_while_refresh_lock_is_held(dev_db):
    """advisory lock은 행·표를 잠그지 않는다: 갱신이 락을 잡은 동안 API 조회·다른 세션의 쓰기가 막히지 않는다."""
    from fastapi.testclient import TestClient

    from app.main import app

    _no_other_refresh(dev_db)
    lock = RR.acquire_lock()
    try:
        r = TestClient(app).get("/api/status")
        assert r.status_code == 200
        dev_db.execute(text("SET LOCAL lock_timeout = '2s'"))
        dev_db.execute(text("UPDATE data_refresh_log SET step = step WHERE id = (SELECT max(id) FROM data_refresh_log)"))
        dev_db.rollback()
    finally:
        lock.release()


# ---------- 실행 기록·복구 ----------

def test_run_lifecycle_and_crash_recovery(dev_db, cleanup):
    _no_other_refresh(dev_db)
    rid = RR.start_run(dev_db, ["fx"], "manual")
    cleanup.append(rid)
    dev_db.execute(text("UPDATE data_refresh_run SET note = 'pytest' WHERE id = :i"), {"i": rid})
    dev_db.commit()
    assert RR.current_run(dev_db)["id"] == rid
    # 프로세스가 끝나지 못하고 사라짐 -> 다음 실행이 락을 잡은 뒤 aborted로 정리
    lock = RR.acquire_lock()
    try:
        assert RR.recover_aborted(dev_db) >= 1
    finally:
        lock.release()
    row = dev_db.execute(text("SELECT status, finished_at FROM data_refresh_run WHERE id = :i"), {"i": rid}).first()
    assert row.status == "aborted" and row.finished_at is not None
    rid2 = RR.start_run(dev_db, ["fx"], "scheduler")
    cleanup.append(rid2)
    RR.finish_run(dev_db, rid2, "partial", 3, note="pytest")
    row = dev_db.execute(text("SELECT status, exit_code, trigger FROM data_refresh_run WHERE id = :i"), {"i": rid2}).first()
    assert tuple(row) == ("partial", 3, "scheduler")


def test_last_success_and_last_failure_are_separate(dev_db, cleanup):
    rid = RR.start_run(dev_db, ["zz_pytest"], "manual")
    cleanup.append(rid)
    now = datetime.now(timezone.utc)
    for i, (status, code) in enumerate([("success", None), ("failed", "network"), ("partial", None)]):
        dev_db.execute(text("INSERT INTO data_refresh_log (step, status, started_at, finished_at, run_id, error_code, retry_count) "
                            "VALUES ('zz_pytest', :s, :t, :t, :r, :c, 1)"),
                       {"s": status, "t": now + timedelta(seconds=i), "r": rid, "c": code})
    dev_db.commit()
    h = RR.step_history(dev_db, ["zz_pytest"])["zz_pytest"]
    assert h["last_attempt"]["status"] == "partial"
    assert h["last_success"]["status"] == "success"
    assert h["last_failure"]["status"] == "partial" and len(h["recent"]) == 3
    assert RR.failed_steps(dev_db, ["zz_pytest"]) == ["zz_pytest"]  # --retry-failed 대상
    RR.set_step_quality(dev_db, rid, "zz_pytest", "warning", 2)
    assert dev_db.execute(text("SELECT count(*) FROM data_refresh_log WHERE run_id = :r AND quality_status = 'warning'"),
                          {"r": rid}).scalar() == 3


def test_prune_keeps_last_success_and_failure_and_never_touches_recent(dev_db, cleanup):
    old = datetime.now(timezone.utc) - timedelta(days=4000)  # 실제 기록보다 훨씬 오래된 합성 행만 대상이 되게
    rows = [("success", old), ("success", old + timedelta(days=1)), ("failed", old + timedelta(days=2)),
            ("failed", old + timedelta(days=3)), ("success", old + timedelta(days=4))]
    for status, t in rows:
        dev_db.execute(text("INSERT INTO data_refresh_log (step, status, started_at, finished_at) VALUES ('zz_pytest', :s, :t, :t)"),
                       {"s": status, "t": t})
    dev_db.commit()
    before = dev_db.execute(text("SELECT count(*) FROM data_refresh_log WHERE step <> 'zz_pytest'")).scalar()
    dry = RR.prune_logs(dev_db, keep_days=3650, dry_run=True)
    assert dry["logs"] == 3 and dev_db.execute(text("SELECT count(*) FROM data_refresh_log WHERE step = 'zz_pytest'")).scalar() == 5
    RR.prune_logs(dev_db, keep_days=3650)
    left = dev_db.execute(text("SELECT status, started_at FROM data_refresh_log WHERE step = 'zz_pytest' ORDER BY started_at")).all()
    assert [r.status for r in left] == ["failed", "success"]  # 마지막 실패·마지막 성공만 남음
    assert dev_db.execute(text("SELECT count(*) FROM data_refresh_log WHERE step <> 'zz_pytest'")).scalar() == before
    with pytest.raises(ValueError):
        RR.prune_logs(dev_db, keep_days=7)


# ---------- 품질 검사·보고서 ----------

def test_quality_checks_run_on_real_schema_and_are_read_only(dev_db):
    counts = {t: dev_db.execute(text(f"SELECT count(*) FROM {t}")).scalar()
              for t in ("stock_price", "market_index", "exchange_rate", "news", "disclosure", "etf_holding")}
    out = DM.check_quality(dev_db, datetime.now(DM.KST))
    for src, v in out.items():
        assert v["status"] in DM.QUALITY
        assert all(c["count"] is not None for c in v["checks"]), (src, v["checks"])  # SQL이 전부 돈다
    assert {t: dev_db.execute(text(f"SELECT count(*) FROM {t}")).scalar() for t in counts} == counts


def test_status_report_on_real_db(dev_db, tmp_path):
    rep = DM.build_report(dev_db, last_runs=RR.recent_runs(dev_db, 5))
    keys = {r["source"] for r in rep["sources"]}
    assert {"stock_price", "market_index", "news", "disclosure", "exchange_rate", "etf_holdings"} <= keys
    for r in rep["sources"]:
        assert r["status"] in DM.STATUSES
    jp, mp = DM.write_reports(rep, tmp_path)
    assert open(mp, encoding="utf-8").read().startswith("# 데이터 갱신 상태 보고서")


# ---------- CLI(실제 락·기록, 가짜 수집 단계) ----------

def test_cli_records_run_and_step_rows_then_rejects_concurrent_run(dev_db, cleanup, monkeypatch):
    from scripts import refresh_data as rd

    _no_other_refresh(dev_db)
    calls = []
    monkeypatch.setattr(rd, "resolve_days", lambda days, today: (10, "test"))
    monkeypatch.setattr(rd, "build_steps", lambda *a, **k: [
        rd.Step(k, k, (lambda k=k: calls.append(k) or (rd.Partial("일부 실패 1/2건") if k == "fx" else None))) for k in rd.ALL_STEPS])
    before = dev_db.execute(text("SELECT coalesce(max(id), 0) FROM data_refresh_run")).scalar()
    assert rd.main(["--only", "fx", "--no-report", "--step-retries", "0"]) == 3  # 부분 성공
    rid = dev_db.execute(text("SELECT max(id) FROM data_refresh_run")).scalar()
    cleanup.append(rid)
    assert rid > before and calls == ["fx"]
    run = dev_db.execute(text("SELECT status, exit_code FROM data_refresh_run WHERE id = :i"), {"i": rid}).first()
    assert tuple(run) == ("partial", 3)
    logs = dict(dev_db.execute(text("SELECT step, status FROM data_refresh_log WHERE run_id = :i"), {"i": rid}).all())
    assert logs["fx"] == "partial" and logs["prices"] == "skipped" and "etf_holdings" not in logs
    # /api/status는 이 실행만 묶어 본다(부분 성공은 ok에 영향 없음)
    from app.data_status import build_status
    lr = build_status(dev_db)["last_refresh"]
    assert lr["ok"] is True and lr["failed_steps"] == []

    lock = RR.acquire_lock()
    try:
        assert rd.main(["--only", "fx", "--no-report"]) == 4  # 다른 실행이 락을 잡고 있으면 아무것도 하지 않는다
    finally:
        lock.release()
    assert calls == ["fx"]


# ---------- 재수집 멱등성(합성 시세, 외부 호출 없음) ----------

def test_recollecting_same_data_is_idempotent_and_failed_attempt_is_harmless(dev_db, cleanup, monkeypatch):
    from app import collectors

    idx = pd.to_datetime(["2026-11-04", "2026-11-05", "2026-11-06"])
    frame = pd.DataFrame({"Close": [1400.0, 1410.5, 1405.25]}, index=idx)
    state = {"fail": True}

    def reader(symbol, start, end):
        if state["fail"]:
            raise ConnectionError("simulated network failure")
        return frame

    monkeypatch.setattr(collectors.fdr, "DataReader", reader)
    monkeypatch.setattr(collectors.http_retry, "call_with_retry", lambda fn, **k: fn())
    r0 = collectors.fetch_and_save_exchange_rate(TEST_PAIR, days=10)
    assert not r0.ok and dev_db.execute(text("SELECT count(*) FROM exchange_rate WHERE pair_code = :p"), {"p": TEST_PAIR}).scalar() == 0
    state["fail"] = False
    r1 = collectors.fetch_and_save_exchange_rate(TEST_PAIR, days=10)
    r2 = collectors.fetch_and_save_exchange_rate(TEST_PAIR, days=10)
    assert r1.ok and r1.saved == 3 and r2.ok and r2.saved == 0
    rows = dev_db.execute(text("SELECT price_date, close_price FROM exchange_rate WHERE pair_code = :p ORDER BY price_date"),
                          {"p": TEST_PAIR}).all()
    assert [(str(d), float(c)) for d, c in rows] == [("2026-11-04", 1400.0), ("2026-11-05", 1410.5), ("2026-11-06", 1405.25)]
