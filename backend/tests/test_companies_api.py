from fastapi.testclient import TestClient

import app.api.companies as companies_api
from app.main import app

client = TestClient(app, raise_server_exceptions=False)

ITEM = {
    "ticker": "005930",
    "name": "삼성전자",
    "market": "KOSPI",
    "sector": None,
    "latest_close": 286500.0,
    "change_pct": 3.24,
}


def mock_search(monkeypatch, result):
    monkeypatch.setattr(companies_api, "search_companies", lambda db, q, limit: result)


class TestGetCompanies:
    def test_default_query_params(self, monkeypatch):
        seen = {}

        def fake(db, q, limit):
            seen["args"] = (q, limit)
            return []

        monkeypatch.setattr(companies_api, "search_companies", fake)
        r = client.get("/api/companies")
        assert r.status_code == 200
        assert seen["args"] == (None, 50)

    def test_success_response_shape(self, monkeypatch):
        mock_search(monkeypatch, [ITEM])
        r = client.get("/api/companies?q=삼성전자")
        assert r.status_code == 200
        assert r.json() == [ITEM]

    def test_passes_q_and_limit_through(self, monkeypatch):
        seen = {}

        def fake(db, q, limit):
            seen["args"] = (q, limit)
            return []

        monkeypatch.setattr(companies_api, "search_companies", fake)
        client.get("/api/companies?q=네이버&limit=20")
        assert seen["args"] == ("네이버", 20)

    def test_limit_out_of_range_is_422(self):
        assert client.get("/api/companies?limit=0").status_code == 422
        assert client.get("/api/companies?limit=301").status_code == 422

    def test_empty_results_is_still_200(self, monkeypatch):
        mock_search(monkeypatch, [])
        r = client.get("/api/companies?q=존재하지않음")
        assert r.status_code == 200
        assert r.json() == []

    def test_q_too_long_is_422(self):
        r = client.get("/api/companies?q=" + "a" * 101)
        assert r.status_code == 422
