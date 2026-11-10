"""GET /api/market/overview(41차, 2026-11-10): 순수 계산(app.market_snapshot), 라우터(mock), 개발 DB 대조(integration).

dev DB 대조는 SQL로 직접 센 값과 API 값을 비교하므로 시세가 없는 CI 빈 DB에서도 그대로 돈다(needs_collected_data 마커 없음).
"""

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

import app.api.market as market_api
from app import market_snapshot as ms
from app.main import app

client = TestClient(app, raise_server_exceptions=False)
KST = ZoneInfo("Asia/Seoul")
AFTER_CLOSE = datetime(2026, 10, 14, 16, 0, tzinfo=KST)  # 수요일 장 마감 뒤(기대 거래일 = 10-14)
INTRADAY = datetime(2026, 10, 14, 10, 0, tzinfo=KST)  # 장중(기대 거래일 = 10-13)
TOP_KEYS = {"as_of", "expected_date", "generated_at", "status", "missing", "freshness", "indices", "breadth",
            "last_collection", "source", "unavailable", "note"}
INDEX_KEYS = {"code", "name", "date", "close", "previous_date", "previous_close", "change", "change_pct",
              "change_unavailable_reason", "first_collected_at", "lag_business_days"}
CREATED = datetime(2026, 10, 14, 19, 0, tzinfo=KST)


def rows(*pairs):
    return [(d, c, CREATED) for d, c in pairs]


class TestIndexItem:
    def test_change_against_previous_trading_day(self):
        it = ms.index_item("KS11", "코스피", rows((date(2026, 10, 14), 2600.5), (date(2026, 10, 13), 2580.0)), AFTER_CLOSE)
        assert it["date"] == date(2026, 10, 14) and it["previous_date"] == date(2026, 10, 13)
        assert it["change"] == 20.5 and it["change_pct"] == round((2600.5 / 2580 - 1) * 100, 2)
        assert it["change_unavailable_reason"] is None and it["lag_business_days"] == 0
        assert it["first_collected_at"] == CREATED.isoformat()
        assert set(it) == INDEX_KEYS

    def test_holiday_between_is_not_a_gap(self):
        """10-09(금, 한글날)·주말을 건너뛴 10-08 -> 10-12는 직전 거래일이라 정상 비교다."""
        it = ms.index_item("KS11", "코스피", rows((date(2026, 10, 12), 110), (date(2026, 10, 8), 100)), AFTER_CLOSE)
        assert it["change"] == 10.0 and it["change_pct"] == 10.0

    def test_missing_trading_day_means_no_change(self):
        """저장 공백(10-13이 없음): 이틀치 변화를 '전일 대비'라고 부르지 않는다. 값은 null이고 이유가 있다."""
        it = ms.index_item("KQ11", "코스닥", rows((date(2026, 10, 14), 110), (date(2026, 10, 12), 100)), AFTER_CLOSE)
        assert it["change"] is None and it["change_pct"] is None
        assert it["change_unavailable_reason"] == ms.REASON_GAP and it["previous_date"] == date(2026, 10, 12)

    def test_single_row(self):
        it = ms.index_item("KS11", "코스피", rows((date(2026, 10, 14), 100)), AFTER_CLOSE)
        assert it["close"] == 100.0 and it["change"] is None and it["change_unavailable_reason"] == ms.REASON_NO_PREVIOUS

    def test_no_rows_is_all_null(self):
        it = ms.index_item("KS11", "코스피", [], AFTER_CLOSE)
        assert it["code"] == "KS11" and all(it[k] is None for k in INDEX_KEYS - {"code", "name"})

    def test_lag_counts_trading_days_only(self):
        """10-08 종가를 10-14 마감 뒤에 보면 10-12·13·14 세 거래일 늦다(10-09 휴장·주말 제외)."""
        assert ms.index_item("KS11", "코스피", rows((date(2026, 10, 8), 1)), AFTER_CLOSE)["lag_business_days"] == 3


class TestBreadth:
    def test_counts_and_ratios(self):
        b = ms.breadth_counts(up=60, down=35, flat=5, missing=2, registered=110)
        assert (b["total"], b["change_missing"], b["no_price"], b["registered"]) == (102, 2, 8, 110)
        assert (b["up_ratio_pct"], b["down_ratio_pct"], b["flat_ratio_pct"]) == (60.0, 35.0, 5.0)  # 분모 100(결측 제외)

    def test_zero_denominator_is_null(self):
        b = ms.breadth_counts(0, 0, 0, 3, 3)
        assert b["up_ratio_pct"] is None and b["total"] == 3 and b["no_price"] == 0

    def test_item_sums_markets(self):
        b = ms.breadth_item(date(2026, 10, 14), {"KOSPI": (10, 5, 1, 0, 16), "KOSDAQ": (2, 3, 0, 1, 7)}, AFTER_CLOSE)
        assert (b["up"], b["down"], b["flat"], b["change_missing"], b["registered"]) == (12, 8, 1, 1, 23)
        assert b["universe"] == "collected_stocks" and set(b["by_market"]) == {"KOSPI", "KOSDAQ"}
        assert b["by_market"]["KOSDAQ"]["total"] == 6

    def test_no_day(self):
        assert ms.breadth_item(None, {}, AFTER_CLOSE) is None


class TestFreshnessAndCutoff:
    @pytest.mark.parametrize("lag,status", [(None, "no_data"), (0, "latest"), (1, "delayed"), (5, "delayed"), (6, "stale")])
    def test_status(self, lag, status):
        assert ms.freshness_status(lag) == status

    def test_cutoff_excludes_today_during_session(self):
        assert ms.cutoff_date(INTRADAY) == date(2026, 10, 13)
        assert ms.cutoff_date(AFTER_CLOSE) == date(2026, 10, 14)
        assert ms.cutoff_date(datetime(2026, 10, 9, 10, 0, tzinfo=KST)) == date(2026, 10, 9)  # 휴장일엔 장이 없다


class TestBuild:
    INDEX = {"KS11": rows((date(2026, 10, 14), 2600), (date(2026, 10, 13), 2590)),
             "KQ11": rows((date(2026, 10, 14), 800), (date(2026, 10, 13), 790))}
    GROUPS = {"KOSPI": (10, 5, 1, 0, 16)}

    def test_ok(self):
        b = ms.build(self.INDEX, date(2026, 10, 14), self.GROUPS, AFTER_CLOSE)
        assert set(b) == TOP_KEYS and b["status"] == "ok" and b["missing"] == []
        assert b["as_of"] == date(2026, 10, 14) and b["expected_date"] == date(2026, 10, 14)
        assert b["freshness"] == {"status": "latest", "lag_business_days": 0, "calendar_unknown_days": 0,
                                  "intraday_excluded": False, "dates_differ": False}
        assert [i["code"] for i in b["indices"]] == ["KS11", "KQ11"]
        assert b["unavailable"] == ["trading_value", "market_volume"]
        assert b["last_collection"] == {"index": None, "prices": None}

    def test_partial_when_one_index_missing(self):
        b = ms.build({"KS11": self.INDEX["KS11"]}, date(2026, 10, 14), self.GROUPS, AFTER_CLOSE)
        assert b["status"] == "partial" and b["missing"] == ["KQ11"] and b["indices"][1]["date"] is None

    def test_partial_when_no_breadth(self):
        b = ms.build(self.INDEX, None, {}, AFTER_CLOSE)
        assert b["status"] == "partial" and b["missing"] == ["breadth"] and b["breadth"] is None

    def test_no_data(self):
        b = ms.build({}, None, {}, AFTER_CLOSE)
        assert b["status"] == "no_data" and b["as_of"] is None and b["freshness"]["status"] == "no_data"
        assert b["missing"] == ["KS11", "KQ11", "breadth"]

    def test_stale_and_dates_differ(self):
        old = {"KS11": rows((date(2026, 10, 1), 1), (date(2026, 9, 30), 1)), "KQ11": self.INDEX["KQ11"]}
        b = ms.build(old, date(2026, 10, 14), self.GROUPS, AFTER_CLOSE)
        assert b["as_of"] == date(2026, 10, 1)  # 더 이른 날
        assert b["freshness"]["status"] == "stale" and b["freshness"]["dates_differ"] is True
        assert b["freshness"]["lag_business_days"] == b["indices"][0]["lag_business_days"] > 5

    def test_intraday_flag(self):
        assert ms.build(self.INDEX, date(2026, 10, 14), self.GROUPS, INTRADAY)["freshness"]["intraday_excluded"] is True

    def test_collection_failure_is_passed_through_without_changing_numbers(self):
        failed = {"step": "index", "status": "failed", "ok": False, "finished_at": None, "error_code": "network"}
        a = ms.build(self.INDEX, date(2026, 10, 14), self.GROUPS, AFTER_CLOSE)
        b = ms.build(self.INDEX, date(2026, 10, 14), self.GROUPS, AFTER_CLOSE, {"index": failed})
        assert b["last_collection"] == {"index": failed, "prices": None}
        assert b["indices"] == a["indices"] and b["status"] == a["status"]


class TestRouter:
    def test_public_and_validates_shape(self, monkeypatch):
        monkeypatch.setattr(market_api.market_snapshot, "load",
                            lambda db, now: ms.build(TestBuild.INDEX, date(2026, 10, 14), TestBuild.GROUPS, AFTER_CLOSE))
        r = client.get("/api/market/overview")  # 토큰 없이
        assert r.status_code == 200
        b = r.json()
        assert set(b) == TOP_KEYS and b["as_of"] == "2026-10-14" and b["indices"][0]["date"] == "2026-10-14"
        assert b["breadth"]["by_market"]["KOSPI"]["up"] == 10

    def test_no_data_is_200(self, monkeypatch):
        monkeypatch.setattr(market_api.market_snapshot, "load", lambda db, now: ms.build({}, None, {}, AFTER_CLOSE))
        r = client.get("/api/market/overview")
        assert r.status_code == 200 and r.json()["status"] == "no_data" and r.json()["breadth"] is None

    def test_only_get(self):
        assert client.post("/api/market/overview").status_code == 405


@pytest.mark.integration
class TestDevDb:
    @pytest.fixture(autouse=True)
    def fixed_now(self, monkeypatch):
        monkeypatch.setattr(market_api, "_now", lambda: datetime(2030, 1, 2, 16, 0, tzinfo=KST))  # 모든 저장 행보다 뒤, 마감 후

    def test_matches_sql(self, dev_db):
        b = client.get("/api/market/overview").json()
        assert set(b) == TOP_KEYS
        for item in b["indices"]:
            got = dev_db.execute(text("SELECT price_date, close_price FROM market_index WHERE index_code = :c "
                                      "ORDER BY price_date DESC LIMIT 2"), {"c": item["code"]}).all()
            if not got:
                assert item["date"] is None and item["code"] in b["missing"]
                continue
            assert item["date"] == got[0][0].isoformat() and item["close"] == float(got[0][1])
            if len(got) == 2:
                assert item["previous_date"] == got[1][0].isoformat()
                if item["change"] is not None:
                    assert item["change"] == round(float(got[0][1]) - float(got[1][1]), 2)
        d = dev_db.execute(text("SELECT max(p.price_date) FROM stock_price p JOIN company c ON c.id = p.company_id "
                                "WHERE c.asset_type = 'stock'")).scalar()
        if d is None:
            assert b["breadth"] is None and "breadth" in b["missing"]
        else:
            up, down, flat, missing = dev_db.execute(text(
                "SELECT count(*) FILTER (WHERE p.change_pct > 0), count(*) FILTER (WHERE p.change_pct < 0), "
                "count(*) FILTER (WHERE p.change_pct = 0), count(*) FILTER (WHERE p.change_pct IS NULL) "
                "FROM stock_price p JOIN company c ON c.id = p.company_id WHERE c.asset_type = 'stock' AND p.price_date = :d"),
                {"d": d}).one()
            br = b["breadth"]
            assert br["date"] == d.isoformat() and (br["up"], br["down"], br["flat"], br["change_missing"]) == (up, down, flat, missing)
            assert sum(m["total"] for m in br["by_market"].values()) == br["total"]
        assert b["status"] in ("ok", "partial", "no_data") and b["freshness"]["status"] in ("latest", "delayed", "stale", "no_data")

    @pytest.mark.parametrize("step", ["index", "prices"])
    def test_last_collection_is_latest_non_skipped_attempt(self, dev_db, step):
        """수집 기록이 없는 CI DB에서는 null, 있으면 skipped를 뺀 가장 최근 시도(id 최대)와 같다."""
        got = client.get("/api/market/overview").json()["last_collection"][step]
        row = dev_db.execute(text("SELECT status, error_code FROM data_refresh_log WHERE step = :s AND status <> 'skipped' "
                                  "ORDER BY id DESC LIMIT 1"), {"s": step}).first()
        if row is None:
            assert got is None
        else:
            assert (got["step"], got["status"], got["ok"], got["error_code"]) == (step, row[0], row[0] != "failed", row[1])

    def test_read_only_four_selects(self, dev_db):
        from sqlalchemy import event

        from app.db.session import engine

        n = []
        listener = lambda *a, **k: n.append(a[2] if len(a) > 2 else "")  # noqa: E731
        event.listen(engine, "before_cursor_execute", listener)
        try:
            assert client.get("/api/market/overview").status_code == 200
        finally:
            event.remove(engine, "before_cursor_execute", listener)
        assert len(n) == 4, n
        assert all(str(q).lstrip().upper().startswith(("SELECT", "WITH")) for q in n)
