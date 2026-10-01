from datetime import datetime

from fastapi.testclient import TestClient

import app.api.watchlist as watchlist_api
from app.main import app
from app.tools.company_resolver import Resolution

client = TestClient(app, raise_server_exceptions=False)

HEADERS = {"X-Device-Id": "test-device-1"}
COMPANY = type("C", (), {"id": 1, "ticker": "005930", "name": "삼성전자"})()
WATCHLIST_ROW = type("W", (), {"created_at": datetime(2026, 10, 1)})()

ITEM = {
    "company_id": 1,
    "ticker": "005930",
    "company_name": "삼성전자",
    "added_at": "2026-10-01T00:00:00+00:00",
    "latest_close": 71000.0,
    "latest_close_date": "2026-09-23",
    "change_pct": 1.23,
}


def mock_resolution(monkeypatch, res):
    monkeypatch.setattr(watchlist_api, "resolve_company", lambda db, ticker: res)


class TestDeviceIdHeader:
    def test_missing_header_is_422_on_every_route(self):
        assert client.get("/api/watchlist").status_code == 422
        assert client.post("/api/watchlist", json={"ticker": "005930"}).status_code == 422
        assert client.delete("/api/watchlist/005930").status_code == 422

    def test_blank_header_is_422(self):
        r = client.get("/api/watchlist", headers={"X-Device-Id": ""})
        assert r.status_code == 422


class TestGetWatchlist:
    def test_returns_items_from_service(self, monkeypatch):
        monkeypatch.setattr(watchlist_api, "list_items", lambda db, device_id: [ITEM])
        r = client.get("/api/watchlist", headers=HEADERS)
        assert r.status_code == 200
        assert r.json() == {"items": [ITEM]}

    def test_passes_through_device_id(self, monkeypatch):
        seen = {}
        monkeypatch.setattr(watchlist_api, "list_items", lambda db, device_id: seen.setdefault("id", device_id) or [])
        client.get("/api/watchlist", headers={"X-Device-Id": "device-xyz"})
        assert seen["id"] == "device-xyz"


class TestAddWatchlistItem:
    def test_success_is_201(self, monkeypatch):
        mock_resolution(monkeypatch, Resolution(company=COMPANY))
        monkeypatch.setattr(watchlist_api, "add_item", lambda db, device_id, company_id: (WATCHLIST_ROW, False))

        r = client.post("/api/watchlist", json={"ticker": "삼성전자"}, headers=HEADERS)

        assert r.status_code == 201
        body = r.json()
        assert body["company_id"] == 1
        assert body["ticker"] == "005930"
        assert body["company_name"] == "삼성전자"

    def test_duplicate_is_409(self, monkeypatch):
        mock_resolution(monkeypatch, Resolution(company=COMPANY))
        monkeypatch.setattr(watchlist_api, "add_item", lambda db, device_id, company_id: (COMPANY, True))

        r = client.post("/api/watchlist", json={"ticker": "005930"}, headers=HEADERS)

        assert r.status_code == 409
        assert "삼성전자" in r.json()["detail"]

    def test_unknown_company_is_404_and_add_item_not_called(self, monkeypatch):
        mock_resolution(monkeypatch, Resolution(message="'없는종목' 종목을 찾지 못했습니다."))
        called = []
        monkeypatch.setattr(watchlist_api, "add_item", lambda *a, **k: called.append(1))

        r = client.post("/api/watchlist", json={"ticker": "없는종목"}, headers=HEADERS)

        assert r.status_code == 404
        assert called == []

    def test_blank_ticker_is_422(self):
        r = client.post("/api/watchlist", json={"ticker": ""}, headers=HEADERS)
        assert r.status_code == 422

    def test_missing_ticker_is_422(self):
        r = client.post("/api/watchlist", json={}, headers=HEADERS)
        assert r.status_code == 422


class TestDeleteWatchlistItem:
    def test_success_is_204(self, monkeypatch):
        mock_resolution(monkeypatch, Resolution(company=COMPANY))
        monkeypatch.setattr(watchlist_api, "remove_item", lambda db, device_id, company_id: True)

        r = client.delete("/api/watchlist/005930", headers=HEADERS)

        assert r.status_code == 204
        assert r.content == b""

    def test_not_registered_is_404(self, monkeypatch):
        mock_resolution(monkeypatch, Resolution(company=COMPANY))
        monkeypatch.setattr(watchlist_api, "remove_item", lambda db, device_id, company_id: False)

        r = client.delete("/api/watchlist/005930", headers=HEADERS)

        assert r.status_code == 404
        assert "삼성전자" in r.json()["detail"]

    def test_unknown_company_is_404(self, monkeypatch):
        mock_resolution(monkeypatch, Resolution(message="'없는종목' 종목을 찾지 못했습니다."))
        r = client.delete("/api/watchlist/없는종목", headers=HEADERS)
        assert r.status_code == 404
