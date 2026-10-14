"""GET /api/status: stale/영업일 계산(순수), 응답 형태(라우터 mock), 개발 DB 실제 응답(integration)."""

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import app.api.status as status_api
from app.data_status import (
    KST,
    business_days_behind,
    is_stale,
    last_business_day,
    summarize_last_run,
)
from app.main import app

client = TestClient(app, raise_server_exceptions=False)

# 2026-10-14는 수요일이다.
WED, TUE, MON, FRI, SAT, SUN = date(2026, 10, 14), date(2026, 10, 13), date(2026, 10, 12), date(2026, 10, 9), date(2026, 10, 10), date(2026, 10, 11)


class TestBusinessDays:
    def test_last_business_day(self):
        assert last_business_day(WED) == WED
        assert last_business_day(SAT) == FRI
        assert last_business_day(SUN) == FRI

    def test_behind_counts_weekdays_only(self):
        assert business_days_behind(WED, WED) == 0
        assert business_days_behind(TUE, WED) == 1
        assert business_days_behind(FRI, MON) == 1  # 주말은 세지 않는다
        assert business_days_behind(FRI, WED) == 3
        assert business_days_behind(date(2026, 9, 17), WED) == 19

    def test_future_date_is_not_behind(self):
        assert business_days_behind(date(2026, 10, 20), WED) == 0


class TestStale:
    def test_same_day_and_one_day_behind_are_fresh(self):
        assert not is_stale(WED, WED)
        assert not is_stale(TUE, WED)

    def test_two_business_days_behind_is_stale(self):
        assert is_stale(MON, WED)

    def test_weekend_does_not_make_friday_stale(self):
        assert not is_stale(FRI, SAT)
        assert not is_stale(FRI, SUN)
        assert not is_stale(FRI, MON)  # 월요일 장 시작 전: 금요일 종가가 최신
        assert is_stale(FRI, date(2026, 10, 13))  # 화요일엔 금요일이 2영업일 전

    def test_missing_data_is_stale(self):
        assert is_stale(None, WED)

    def test_stuck_index_case(self):
        assert is_stale(date(2026, 9, 17), WED)


def _row(step, status, finished):
    return SimpleNamespace(step=step, status=status, finished_at=finished)


class TestSummarizeLastRun:
    def test_no_rows_is_none(self):
        assert summarize_last_run([]) is None

    def test_all_success(self):
        t = datetime(2026, 10, 14, 17, 40, tzinfo=timezone.utc)
        rows = [_row("content", "success", t), _row("embed", "skipped", t - timedelta(minutes=1)), _row("news", "success", t)]
        out = summarize_last_run(rows)
        assert out["ok"] is True and out["failed_steps"] == []
        assert out["finished_at"] == t.astimezone(KST).isoformat()

    def test_failed_steps_listed_in_run_order(self):
        t = datetime(2026, 10, 14, 8, 0, tzinfo=timezone.utc)
        rows = [_row("content", "failed", t), _row("embed", "failed", t), _row("news", "success", t)]
        out = summarize_last_run(rows)
        assert out["ok"] is False and out["failed_steps"] == ["embed", "content"]

    def test_previous_run_is_not_mixed_in(self):
        # 매 실행이 6단계를 모두 기록하므로, 같은 step이 다시 나오는 지점이 이전 실행의 경계다.
        t = datetime(2026, 10, 14, 8, 0, tzinfo=timezone.utc)
        old = t - timedelta(days=1)
        steps = ["content", "embed", "news", "fx", "index", "prices"]  # id 내림차순
        rows = [_row(s, "success", t) for s in steps] + [_row(s, "failed", old) for s in steps]
        out = summarize_last_run(rows)
        assert out["ok"] is True and out["failed_steps"] == []


FAKE_STATUS = {
    "generated_at": "2026-10-14T17:30:00+09:00",
    "sources": {
        "stock_price": {"latest_date": date(2026, 10, 14)},
        "etf_price": {"latest_date": date(2026, 10, 14)},
        "market_index": {"latest_date": date(2026, 10, 14), "stale": False},
        "exchange_rate": {"latest_date": date(2026, 10, 14)},
        "news": {"latest_published_date": date(2026, 10, 14)},
        "disclosure": {"latest_date": date(2026, 10, 13)},
    },
    "last_refresh": None,
    "is_realtime": False,
    "note": "주가는 종가 기준이며, 실시간 시세는 별도 엔드포인트로 제공됩니다.",
}


class TestRouter:
    def test_public_and_shape(self, monkeypatch):
        monkeypatch.setattr(status_api, "build_status", lambda db: FAKE_STATUS)
        r = client.get("/api/status")  # 토큰 없이
        assert r.status_code == 200
        body = r.json()
        assert set(body) == {"generated_at", "sources", "last_refresh", "is_realtime", "note"}
        assert set(body["sources"]) == {"stock_price", "etf_price", "market_index", "exchange_rate", "news", "disclosure"}
        assert body["sources"]["market_index"] == {"latest_date": "2026-10-14", "stale": False}
        assert body["sources"]["news"] == {"latest_published_date": "2026-10-14"}
        assert body["is_realtime"] is False

    def test_no_refresh_log_is_null(self, monkeypatch):
        monkeypatch.setattr(status_api, "build_status", lambda db: FAKE_STATUS)
        assert client.get("/api/status").json()["last_refresh"] is None

    def test_last_refresh_shape(self, monkeypatch):
        last = {"finished_at": "2026-10-14T17:50:00+09:00", "ok": False, "failed_steps": ["embed"]}
        monkeypatch.setattr(status_api, "build_status", lambda db: {**FAKE_STATUS, "last_refresh": last})
        assert client.get("/api/status").json()["last_refresh"] == last

    def test_missing_source_dates_are_null(self, monkeypatch):
        s = {**FAKE_STATUS, "sources": {**FAKE_STATUS["sources"], "etf_price": {"latest_date": None}}}
        monkeypatch.setattr(status_api, "build_status", lambda db: s)
        assert client.get("/api/status").json()["sources"]["etf_price"] == {"latest_date": None}

    def test_only_get(self):
        assert client.post("/api/status").status_code == 405


@pytest.mark.integration
class TestDevDb:
    """마이그레이션이 적용된 개발 DB의 실제 응답(읽기 전용). DB가 꺼져 있으면 건너뛴다."""

    def test_real_response(self, dev_db):
        r = client.get("/api/status")
        assert r.status_code == 200
        body = r.json()
        assert set(body["sources"]) == {"stock_price", "etf_price", "market_index", "exchange_rate", "news", "disclosure"}
        assert body["is_realtime"] is False
        assert body["generated_at"].endswith("+09:00")
        assert body["sources"]["stock_price"]["latest_date"] is not None
        assert isinstance(body["sources"]["market_index"]["stale"], bool)
        lr = body["last_refresh"]
        assert lr is None or set(lr) == {"finished_at", "ok", "failed_steps"}

    def test_query_count_is_small(self, dev_db):
        from sqlalchemy import event

        from app.data_status import build_status
        from app.db.session import engine

        n = []
        listener = lambda *a, **k: n.append(1)  # noqa: E731
        event.listen(engine, "before_cursor_execute", listener)
        try:
            build_status(dev_db)
        finally:
            event.remove(engine, "before_cursor_execute", listener)
        assert len(n) <= 8
