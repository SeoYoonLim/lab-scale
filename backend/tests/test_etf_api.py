"""ETF(asset_type) 관련 라우트 동작(DB 없이 mock): 검색 필터, discovery 기본 stock-only, 관심종목/포트폴리오/체결내역의 asset_type.
실제 DB 동작은 test_etf_devdb.py."""

import pytest
from fastapi.testclient import TestClient

import app.api.companies as companies_api
import app.api.discovery as discovery_api
import app.api.portfolio as portfolio_api
import app.api.watchlist as watchlist_api
from app.main import app
from app.tools.company_resolver import Resolution

client = TestClient(app, raise_server_exceptions=False)

ETF_COMPANY = type("C", (), {"id": 351, "ticker": "069500", "name": "KODEX 200", "asset_type": "etf"})()
STOCK_ITEM = {"ticker": "005930", "name": "삼성전자", "market": "KOSPI", "sector": None, "asset_type": "stock",
              "latest_close": 286500.0, "change_pct": 3.24}
ETF_ITEM = {"ticker": "069500", "name": "KODEX 200", "market": None, "sector": None, "asset_type": "etf",
            "latest_close": 96270.0, "change_pct": 1.07}


@pytest.fixture(autouse=True)
def _logged_in(login_as):
    login_as(7)


class TestCompaniesAssetType:
    @pytest.fixture
    def seen(self, monkeypatch):
        seen = {}

        def fake(db, q, limit, asset_type=None):
            seen.update(q=q, limit=limit, asset_type=asset_type)
            return [i for i in (STOCK_ITEM, ETF_ITEM) if asset_type in (None, i["asset_type"])]

        monkeypatch.setattr(companies_api, "search_companies", fake)
        return seen

    def test_no_filter_returns_everything(self, seen):
        body = client.get("/api/companies").json()
        assert seen["asset_type"] is None
        assert [i["asset_type"] for i in body] == ["stock", "etf"]

    @pytest.mark.parametrize("value", ["stock", "etf"])
    def test_filter_is_passed_through(self, seen, value):
        body = client.get("/api/companies", params={"asset_type": value}).json()
        assert seen["asset_type"] == value
        assert {i["asset_type"] for i in body} == {value}

    def test_etf_item_has_null_market_and_sector_and_keeps_old_fields(self, seen):
        (item,) = client.get("/api/companies", params={"asset_type": "etf"}).json()
        assert item == ETF_ITEM
        assert set(item) == {"ticker", "name", "market", "sector", "asset_type", "latest_close", "change_pct"}

    def test_stock_item_keeps_every_old_field(self, seen):
        (item,) = client.get("/api/companies", params={"asset_type": "stock"}).json()
        assert {k: v for k, v in item.items() if k != "asset_type"} == {k: v for k, v in STOCK_ITEM.items() if k != "asset_type"}

    @pytest.mark.parametrize("bad", ["bond", "ETF", "", "all"])
    def test_invalid_asset_type_is_422(self, seen, bad):
        assert client.get("/api/companies", params={"asset_type": bad}).status_code == 422
        assert seen == {}

    def test_q_and_asset_type_together(self, seen):
        client.get("/api/companies", params={"q": "코덱스", "asset_type": "etf", "limit": 5})
        assert seen == {"q": "코덱스", "limit": 5, "asset_type": "etf"}

    def test_item_without_asset_type_defaults_to_stock(self, monkeypatch):
        old = {k: v for k, v in STOCK_ITEM.items() if k != "asset_type"}
        monkeypatch.setattr(companies_api, "search_companies", lambda db, q, limit, asset_type=None: [old])
        assert client.get("/api/companies").json()[0]["asset_type"] == "stock"


TRENDING = {
    "category": "gainers", "asset_type": "stock", "found": True, "price_date": "2026-10-08", "window_days": 20,
    "items": [{"ticker": "440110", "company_name": "파두", "close_price": 81400.0, "change_pct": 12.59, "volume": 1,
               "avg_volume": 1.0, "volume_ratio": 2.0}],
}


class TestDiscoveryAssetType:
    @pytest.fixture
    def seen(self, monkeypatch):
        seen = {}

        def fake(db, category, limit, asset_type="stock"):
            seen.update(category=category, limit=limit, asset_type=asset_type)
            return {**TRENDING, "asset_type": asset_type}

        monkeypatch.setattr(discovery_api, "get_trending", fake)
        return seen

    def test_default_is_stock_only(self, seen):
        body = client.get("/api/discovery/trending").json()
        assert seen["asset_type"] == "stock" and body["asset_type"] == "stock"

    def test_etf_can_be_requested(self, seen):
        body = client.get("/api/discovery/trending", params={"asset_type": "etf", "category": "losers", "limit": 3}).json()
        assert seen == {"category": "losers", "limit": 3, "asset_type": "etf"} and body["asset_type"] == "etf"

    def test_existing_fields_are_kept(self, seen):
        body = client.get("/api/discovery/trending").json()
        assert set(body) == {"category", "asset_type", "price_date", "window_days", "items"}
        assert body["items"][0]["ticker"] == "440110"

    @pytest.mark.parametrize("bad", ["bond", "ETF", "all", ""])
    def test_invalid_asset_type_is_422(self, seen, bad):
        assert client.get("/api/discovery/trending", params={"asset_type": bad}).status_code == 422
        assert seen == {}


WATCH_ITEM = {"company_id": 351, "ticker": "069500", "company_name": "KODEX 200", "asset_type": "etf",
              "added_at": "2026-10-14T00:00:00+00:00", "latest_close": 96270.0, "latest_close_date": "2026-10-14",
              "change_pct": 1.07}


class TestWatchlistEtf:
    def test_list_shows_asset_type(self, monkeypatch):
        monkeypatch.setattr(watchlist_api, "list_items", lambda db, key: [WATCH_ITEM])
        assert client.get("/api/watchlist").json()["items"] == [WATCH_ITEM]

    def test_add_etf_by_korean_brand_name(self, monkeypatch):
        monkeypatch.setattr(watchlist_api, "resolve_company",
                            lambda db, t: Resolution(company=ETF_COMPANY, corrected_from=t))
        monkeypatch.setattr(watchlist_api, "add_item", lambda db, key, cid: (type("W", (), {})(), False))
        monkeypatch.setattr(watchlist_api, "added_item", lambda db, w, c: WATCH_ITEM)
        r = client.post("/api/watchlist", json={"ticker": "코덱스200"})
        assert r.status_code == 201 and r.json() == WATCH_ITEM

    def test_duplicate_etf_is_409(self, monkeypatch):
        monkeypatch.setattr(watchlist_api, "resolve_company",
                            lambda db, t: Resolution(company=ETF_COMPANY))
        monkeypatch.setattr(watchlist_api, "add_item", lambda db, key, cid: (type("W", (), {})(), True))
        r = client.post("/api/watchlist", json={"ticker": "KODEX 200"})
        assert r.status_code == 409 and "KODEX 200" in r.json()["detail"]


ETF_HOLDING = {"ticker": "069500", "company_name": "KODEX 200", "asset_type": "etf", "quantity": 2, "avg_price": 96000.0,
               "current_price": 96270.0, "is_realtime": True, "eval_amount": 192540.0, "profit_loss": 540.0,
               "profit_loss_pct": 0.28}
ETF_TRADE = {"id": 9, "ticker": "069500", "company_name": "KODEX 200", "asset_type": "etf", "side": "buy", "quantity": 2,
             "price": 96000.0, "amount": 192000.0, "executed_at": "2026-10-14T01:00:00+00:00"}


class TestPortfolioEtf:
    def test_holdings_carry_asset_type(self, monkeypatch):
        pf = {"cash_balance": 9_808_000.0, "holdings": [ETF_HOLDING], "total_eval_amount": 192540.0, "total_asset": 10_000_540.0}
        monkeypatch.setattr(portfolio_api, "get_portfolio", lambda db, key: pf)
        h = client.get("/api/portfolio").json()["holdings"][0]
        assert h == ETF_HOLDING

    def test_trades_carry_asset_type(self, monkeypatch):
        monkeypatch.setattr(portfolio_api, "list_trades",
                            lambda db, key, limit, offset, company_id=None: {"total": 1, "limit": limit, "offset": offset, "items": [ETF_TRADE]})
        assert client.get("/api/portfolio/trades").json()["items"] == [ETF_TRADE]

    def test_holding_without_asset_type_defaults_to_stock(self, monkeypatch):
        old = {k: v for k, v in ETF_HOLDING.items() if k != "asset_type"}
        monkeypatch.setattr(portfolio_api, "get_portfolio", lambda db, key: {
            "cash_balance": 1.0, "holdings": [old], "total_eval_amount": 0.0, "total_asset": 1.0})
        assert client.get("/api/portfolio").json()["holdings"][0]["asset_type"] == "stock"

    def test_order_for_etf_goes_through_the_same_path(self, monkeypatch):
        monkeypatch.setattr(portfolio_api, "resolve_company", lambda db, t: Resolution(company=ETF_COMPANY))
        seen = {}

        def fake_order(db, key, company, side, quantity):
            seen.update(ticker=company.ticker, side=side, quantity=quantity)
            return {"ticker": "069500", "company_name": "KODEX 200", "side": side, "quantity": quantity, "price": 96000.0,
                    "executed_at": "2026-10-14T01:00:00+00:00", "cash_balance": 9_808_000.0,
                    "holding": {"quantity": quantity, "avg_price": 96000.0}}

        monkeypatch.setattr(portfolio_api, "place_order", fake_order)
        r = client.post("/api/portfolio/orders", json={"ticker": "코덱스200", "side": "buy", "quantity": 2})
        assert r.status_code == 200 and seen == {"ticker": "069500", "side": "buy", "quantity": 2}
        assert r.json()["company_name"] == "KODEX 200"
