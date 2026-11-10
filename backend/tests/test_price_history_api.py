"""GET /api/stocks/{ticker}/price-history: 순수 로직, 라우터(mock), 개발 DB(integration)."""

from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

import app.api.stocks as stocks_api
from app.main import app
from app.price_history import build_price_history

client = TestClient(app, raise_server_exceptions=False)

ITEM_KEYS = {"date", "close", "volume", "change_pct", "open", "high", "low"}
BODY_KEYS = {"ticker", "company_name", "asset_type", "days", "count", "price_as_of", "available_count", "first_date",
             "missing_trading_days", "items"}  # 41차(2026-11-10): 뒤의 셋 추가


def row(day, close, volume=1000, pct=None, o=None, h=None, low=None):
    return SimpleNamespace(
        price_date=day, close_price=Decimal(str(close)), volume=volume,
        change_pct=Decimal(str(pct)) if pct is not None else None,
        open_price=Decimal(str(o)) if o is not None else None,
        high_price=Decimal(str(h)) if h is not None else None,
        low_price=Decimal(str(low)) if low is not None else None,
    )


COMPANY = SimpleNamespace(id=1, ticker="005930", name="삼성전자", asset_type="stock")
# 최신순(조회 쿼리가 돌려주는 순서)
ROWS_DESC = [row(date(2026, 10, 14), 300, 30, 3.0, 290, 305, 289), row(date(2026, 10, 13), 291.5, 20, -1.5), row(date(2026, 10, 12), 296, None)]


class TestBuildPriceHistory:
    def test_items_are_ascending_by_date(self):
        b = build_price_history(COMPANY, ROWS_DESC, 90)
        assert [i["date"] for i in b["items"]] == [date(2026, 10, 12), date(2026, 10, 13), date(2026, 10, 14)]

    def test_fewer_rows_than_days_returns_what_exists(self):
        b = build_price_history(COMPANY, ROWS_DESC, 90)
        assert b["days"] == 90 and b["count"] == 3 and len(b["items"]) == 3
        assert b["price_as_of"] == date(2026, 10, 14)

    def test_only_most_recent_days_rows_are_used(self):
        b = build_price_history(COMPANY, ROWS_DESC, 2)
        assert b["count"] == 2 and [i["date"] for i in b["items"]] == [date(2026, 10, 13), date(2026, 10, 14)]

    def test_no_rows(self):
        b = build_price_history(COMPANY, [], 90)
        assert b["items"] == [] and b["count"] == 0 and b["price_as_of"] is None and b["days"] == 90

    def test_missing_values_are_none_and_numbers_are_float(self):
        i = build_price_history(COMPANY, ROWS_DESC, 90)["items"]
        assert i[0] == {"date": date(2026, 10, 12), "close": 296.0, "volume": None, "change_pct": None, "open": None, "high": None, "low": None}
        assert i[2]["close"] == 300.0 and isinstance(i[2]["close"], float)
        assert i[2]["open"] == 290.0 and i[2]["high"] == 305.0 and i[2]["low"] == 289.0 and i[2]["change_pct"] == 3.0
        assert i[1]["close"] == 291.5 and i[1]["volume"] == 20

    def test_company_fields_and_keys(self):
        b = build_price_history(SimpleNamespace(ticker="069500", name="KODEX 200", asset_type="etf"), ROWS_DESC, 5)
        assert set(b) == BODY_KEYS and set(b["items"][0]) == ITEM_KEYS
        assert (b["ticker"], b["company_name"], b["asset_type"]) == ("069500", "KODEX 200", "etf")

    def test_available_count_and_first_date_default_to_rows(self):
        b = build_price_history(COMPANY, ROWS_DESC, 2)
        assert b["available_count"] == 3 and b["first_date"] == date(2026, 10, 12)
        b = build_price_history(COMPANY, ROWS_DESC, 2, available_count=733, first_date=date(2023, 10, 30))
        assert b["count"] == 2 and b["available_count"] == 733 and b["first_date"] == date(2023, 10, 30)

    def test_missing_trading_days_uses_krx_calendar(self):
        """2026-10-12(월)~10-14(수)는 모두 거래일이라 0. 10-13이 빠지면 1. 주말·휴장일(10-09 한글날)은 결측이 아니다."""
        assert build_price_history(COMPANY, ROWS_DESC, 90)["missing_trading_days"] == 0
        gap = [ROWS_DESC[0], ROWS_DESC[2]]
        assert build_price_history(COMPANY, gap, 90)["missing_trading_days"] == 1
        over_holiday = [row(date(2026, 10, 12), 1), row(date(2026, 10, 8), 1)]  # 10-09 금 한글날, 10-10·11 주말
        assert build_price_history(COMPANY, over_holiday, 90)["missing_trading_days"] == 0

    def test_does_not_mutate_input(self):
        rows = list(ROWS_DESC)
        build_price_history(COMPANY, rows, 90)
        assert rows == ROWS_DESC


@pytest.fixture
def patched(monkeypatch):
    calls = []
    monkeypatch.setattr(stocks_api, "resolve_company", lambda db, t: SimpleNamespace(company=COMPANY, message=None))

    def fake_rows(db, company_id, limit):
        calls.append((company_id, limit))
        return ROWS_DESC[:limit], len(ROWS_DESC), ROWS_DESC[-1].price_date

    monkeypatch.setattr(stocks_api, "history_rows", fake_rows)
    return calls


class TestRouter:
    def test_public_and_shape(self, patched):
        r = client.get("/api/stocks/005930/price-history")  # 토큰 없이
        assert r.status_code == 200
        b = r.json()
        assert set(b) == BODY_KEYS
        assert b["ticker"] == "005930" and b["company_name"] == "삼성전자" and b["asset_type"] == "stock"
        assert b["days"] == 90 and b["count"] == 3 and b["price_as_of"] == "2026-10-14"
        assert [i["date"] for i in b["items"]] == ["2026-10-12", "2026-10-13", "2026-10-14"]
        assert set(b["items"][0]) == ITEM_KEYS
        assert b["items"][0]["volume"] is None and b["items"][0]["open"] is None
        assert b["items"][2]["close"] == 300.0

    def test_default_and_explicit_days_reach_query(self, patched):
        client.get("/api/stocks/005930/price-history")
        client.get("/api/stocks/005930/price-history?days=2")
        assert patched == [(1, 90), (1, 2)]
        assert client.get("/api/stocks/005930/price-history?days=2").json()["days"] == 2

    # 41차(2026-11-10): 상한 90 -> 365. 91은 이제 정상이고 경계 밖은 366이다.
    @pytest.mark.parametrize("qs", ["days=0", "days=366", "days=1000", "days=-1", "days=abc", "days=1.5"])
    def test_invalid_days_is_422(self, patched, qs):
        assert client.get(f"/api/stocks/005930/price-history?{qs}").status_code == 422
        assert patched == []

    @pytest.mark.parametrize("days", [1, 5, 20, 60, 90, 91, 120, 180, 365])
    def test_boundary_days_ok(self, patched, days):
        r = client.get(f"/api/stocks/005930/price-history?days={days}")
        assert r.status_code == 200 and r.json()["days"] == days
        assert patched == [(1, days)]

    def test_new_fields_from_query(self, patched):
        b = client.get("/api/stocks/005930/price-history?days=2").json()
        # 요청 days와 실제 반환 수(count)와 저장 행 전체 수(available_count)는 서로 다른 값이다
        assert (b["days"], b["count"], b["available_count"]) == (2, 2, 3)
        assert b["first_date"] == "2026-10-12" and b["missing_trading_days"] == 0

    def test_no_price_rows_is_200_with_empty_items(self, monkeypatch):
        monkeypatch.setattr(stocks_api, "resolve_company", lambda db, t: SimpleNamespace(company=COMPANY, message=None))
        monkeypatch.setattr(stocks_api, "history_rows", lambda db, cid, limit: ([], 0, None))
        r = client.get("/api/stocks/005930/price-history")
        assert r.status_code == 200
        b = r.json()
        assert b["items"] == [] and b["count"] == 0 and b["price_as_of"] is None and b["days"] == 90
        assert b["available_count"] == 0 and b["first_date"] is None and b["missing_trading_days"] == 0

    def test_unknown_company_is_404_with_detail(self, monkeypatch):
        monkeypatch.setattr(
            stocks_api, "resolve_company", lambda db, t: SimpleNamespace(company=None, message="'없는종목' 종목을 찾지 못했습니다.")
        )
        r = client.get("/api/stocks/없는종목/price-history")
        assert r.status_code == 404 and "찾지 못했습니다" in r.json()["detail"]

    def test_only_get(self):
        assert client.post("/api/stocks/005930/price-history").status_code == 405


@pytest.mark.integration
class TestDevDb:
    @staticmethod
    def _sql_rows(dev_db, ticker, n):
        """API와 별개로 직접 SQL로 조회한 최근 n행(오름차순)."""
        rows = dev_db.execute(
            text(
                "SELECT p.price_date, p.close_price, p.volume, p.change_pct, p.open_price, p.high_price, p.low_price "
                "FROM stock_price p JOIN company c ON c.id = p.company_id "
                "WHERE c.ticker = :t ORDER BY p.price_date DESC LIMIT :n"
            ),
            {"t": ticker, "n": n},
        ).all()
        f = lambda v: float(v) if v is not None else None  # noqa: E731
        return [
            {"date": r[0].isoformat(), "close": f(r[1]), "volume": r[2], "change_pct": f(r[3]),
             "open": f(r[4]), "high": f(r[5]), "low": f(r[6])}
            for r in reversed(rows)
        ]

    @pytest.mark.parametrize("ticker,name,asset_type", [("005930", "삼성전자", "stock"), ("069500", None, "etf")])
    @pytest.mark.needs_collected_data
    def test_matches_stock_price_table(self, dev_db, ticker, name, asset_type):
        b = client.get(f"/api/stocks/{ticker}/price-history?days=30").json()
        expected = self._sql_rows(dev_db, ticker, 30)
        assert b["ticker"] == ticker and b["asset_type"] == asset_type and b["days"] == 30
        if name:
            assert b["company_name"] == name
        assert b["items"] == expected
        assert b["count"] == len(expected) == 30
        assert b["price_as_of"] == expected[-1]["date"]
        dates = [i["date"] for i in b["items"]]
        assert dates == sorted(dates) and len(set(dates)) == len(dates)

    @pytest.mark.parametrize("ticker", ["005930", "069500"])
    @pytest.mark.needs_collected_data
    def test_boundaries_days_1_and_90(self, dev_db, ticker):
        one = client.get(f"/api/stocks/{ticker}/price-history?days=1").json()
        assert one["count"] == 1 and one["items"] == self._sql_rows(dev_db, ticker, 1)
        full = client.get(f"/api/stocks/{ticker}/price-history?days=90").json()
        expected = self._sql_rows(dev_db, ticker, 90)
        assert full["days"] == 90 and full["items"] == expected and full["count"] == len(expected) <= 90
        assert full["items"][-1] == one["items"][0]  # 가장 최근 거래일이 같다

    @pytest.mark.parametrize("days", [120, 180, 365])
    def test_long_ranges_match_table_and_counts(self, dev_db, days):
        """41차: 90 초과도 같은 규칙(최근 N행, 오름차순). available_count·first_date는 SQL 집계와 같다. 시세가 없는 CI DB에서도
        (빈 items·0·null로) 그대로 맞는다."""
        b = client.get(f"/api/stocks/005930/price-history?days={days}").json()
        expected = self._sql_rows(dev_db, "005930", days)
        n, first = dev_db.execute(text("SELECT count(*), min(p.price_date) FROM stock_price p JOIN company c ON c.id = p.company_id "
                                       "WHERE c.ticker = '005930'")).one()
        assert b["days"] == days and b["items"] == expected and b["count"] == len(expected) == min(days, n)
        assert b["available_count"] == n and b["first_date"] == (first.isoformat() if first else None)
        assert b["missing_trading_days"] >= 0

    def test_default_is_90(self, dev_db):
        b = client.get("/api/stocks/005930/price-history").json()
        assert b["days"] == 90 and b["items"] == self._sql_rows(dev_db, "005930", 90)

    def test_name_and_alias_lookup(self, dev_db):
        assert client.get("/api/stocks/삼성전자/price-history?days=1").json()["ticker"] == "005930"

    def test_unknown_ticker_404(self, dev_db):
        r = client.get("/api/stocks/999999/price-history")
        assert r.status_code == 404 and isinstance(r.json()["detail"], str)

    def test_query_count_is_two(self, dev_db):
        """종목 확인(전체 목록 1회) + 시계열 1회 외 추가 쿼리가 없다."""
        from sqlalchemy import event

        from app.db.session import engine

        n = []
        listener = lambda *a, **k: n.append(a[2] if len(a) > 2 else "")  # noqa: E731
        event.listen(engine, "before_cursor_execute", listener)
        try:
            assert client.get("/api/stocks/005930/price-history").status_code == 200
        finally:
            event.remove(engine, "before_cursor_execute", listener)
        assert len(n) == 2, n
        assert all(str(q).lstrip().upper().startswith("SELECT") for q in n)
