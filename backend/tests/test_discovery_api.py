from fastapi.testclient import TestClient

import app.api.discovery as discovery_api
from app.main import app

client = TestClient(app, raise_server_exceptions=False)

TRENDING_RESULT = {
    "category": "gainers",
    "found": True,
    "price_date": "2026-09-23",
    "window_days": 20,
    "items": [
        {
            "ticker": "440110",
            "company_name": "파두",
            "close_price": 81400.0,
            "change_pct": 12.59,
            "volume": 1278892,
            "avg_volume": 429128.75,
            "volume_ratio": 2.98,
        }
    ],
}

NOT_FOUND_RESULT = {
    "category": "gainers",
    "found": False,
    "message": "주가 데이터가 아직 수집되지 않았습니다.",
}


def mock_trending(monkeypatch, result):
    monkeypatch.setattr(discovery_api, "get_trending", lambda db, category, limit: result)


class TestGetTrending:
    def test_default_query_params(self, monkeypatch):
        seen = {}

        def fake(db, category, limit):
            seen["args"] = (category, limit)
            return TRENDING_RESULT

        monkeypatch.setattr(discovery_api, "get_trending", fake)
        r = client.get("/api/discovery/trending")
        assert r.status_code == 200
        assert seen["args"] == ("gainers", 10)

    def test_success_response_shape(self, monkeypatch):
        mock_trending(monkeypatch, TRENDING_RESULT)
        r = client.get("/api/discovery/trending?category=gainers&limit=5")
        assert r.status_code == 200
        body = r.json()
        assert body["category"] == "gainers"
        assert body["price_date"] == "2026-09-23"
        assert body["window_days"] == 20
        assert body["items"][0]["ticker"] == "440110"
        assert body["items"][0]["volume_ratio"] == 2.98

    def test_passes_category_and_limit_through(self, monkeypatch):
        seen = {}

        def fake(db, category, limit):
            seen["args"] = (category, limit)
            return {**TRENDING_RESULT, "category": category}

        monkeypatch.setattr(discovery_api, "get_trending", fake)
        r = client.get("/api/discovery/trending?category=volume_surge&limit=3")
        assert r.status_code == 200
        assert seen["args"] == ("volume_surge", 3)

    def test_invalid_category_is_422(self):
        r = client.get("/api/discovery/trending?category=bogus")
        assert r.status_code == 422

    def test_limit_out_of_range_is_422(self):
        assert client.get("/api/discovery/trending?limit=0").status_code == 422
        assert client.get("/api/discovery/trending?limit=51").status_code == 422

    def test_not_found_is_503(self, monkeypatch):
        mock_trending(monkeypatch, NOT_FOUND_RESULT)
        r = client.get("/api/discovery/trending")
        assert r.status_code == 503
        assert "데이터" in r.json()["detail"]

    def test_empty_items_is_still_200(self, monkeypatch):
        # volume_surge 조건을 만족하는 종목이 하나도 없는 날도 정상(found=True, items=[])이지 에러가 아니다.
        mock_trending(monkeypatch, {**TRENDING_RESULT, "items": []})
        r = client.get("/api/discovery/trending?category=volume_surge")
        assert r.status_code == 200
        assert r.json()["items"] == []
