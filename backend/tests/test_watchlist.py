"""app.watchlist의 응답 항목 조립(item_dict/added_item) 단위 테스트. DB 없이 가짜 행으로 확인한다."""

from datetime import date, datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import app.watchlist as watchlist
from app.watchlist import added_item, item_dict

COMPANY = SimpleNamespace(id=1, ticker="005930", name="삼성전자")
ROW = SimpleNamespace(created_at=datetime(2026, 10, 1, tzinfo=timezone.utc))
PRICE = SimpleNamespace(
    company_id=1, close_price=Decimal("263000.00"), price_date=date(2026, 10, 8), change_pct=Decimal("-2.23")
)


class TestItemDict:
    def test_with_price_fills_latest_close_fields_as_plain_json_types(self):
        item = item_dict(ROW, COMPANY, PRICE)
        assert item == {
            "company_id": 1,
            "ticker": "005930",
            "company_name": "삼성전자",
            "added_at": "2026-10-01T00:00:00+00:00",
            "latest_close": 263000.0,
            "latest_close_date": "2026-10-08",
            "change_pct": -2.23,
        }
        assert isinstance(item["latest_close"], float)
        assert isinstance(item["change_pct"], float)

    def test_without_price_keeps_the_fields_as_none(self):
        item = item_dict(ROW, COMPANY, None)
        assert (item["latest_close"], item["latest_close_date"], item["change_pct"]) == (None, None, None)
        assert item["ticker"] == "005930"

    def test_price_without_change_pct(self):
        price = SimpleNamespace(close_price=Decimal("100"), price_date=date(2026, 10, 8), change_pct=None)
        item = item_dict(ROW, COMPANY, price)
        assert item["latest_close"] == 100.0
        assert item["change_pct"] is None


class TestAddedItem:
    def test_looks_up_only_that_company_and_uses_its_latest_price(self, monkeypatch):
        seen = []
        monkeypatch.setattr(watchlist, "_latest_prices", lambda db, ids: seen.append(ids) or {1: PRICE})

        item = added_item("db", ROW, COMPANY)

        assert seen == [[1]]
        assert item == item_dict(ROW, COMPANY, PRICE)

    def test_company_without_any_stored_price(self, monkeypatch):
        monkeypatch.setattr(watchlist, "_latest_prices", lambda db, ids: {})
        assert added_item("db", ROW, COMPANY)["latest_close"] is None
