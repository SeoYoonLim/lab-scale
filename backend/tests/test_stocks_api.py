from fastapi.testclient import TestClient

import app.api.stocks as stocks_api
from app.main import app
from app.tools.company_resolver import Resolution

client = TestClient(app, raise_server_exceptions=False)

COMPANY = type("C", (), {"ticker": "005930", "name": "삼성전자"})()

REALTIME_DATA = {
    "current_price": 268000.0,
    "change_amount": -500.0,
    "change_pct": -0.19,
    "as_of": "2026-10-01T11:14:11.124728+09:00",
    "is_realtime": True,
    "source": "naver",
}

FALLBACK_DATA = {
    "current_price": 71000.0,
    "change_amount": None,
    "change_pct": 1.23,
    "as_of": "2026-09-23",
    "is_realtime": False,
    "source": "fallback",
}


def mock_resolution(monkeypatch, res):
    monkeypatch.setattr(stocks_api, "resolve_company", lambda db, ticker: res)


def mock_price_data(monkeypatch, data):
    monkeypatch.setattr(stocks_api, "get_realtime_price_data", lambda db, company: data)


class TestRealtimePriceRoute:
    def test_realtime_success_shape(self, monkeypatch):
        mock_resolution(monkeypatch, Resolution(company=COMPANY))
        mock_price_data(monkeypatch, REALTIME_DATA)

        r = client.get("/api/stocks/005930/realtime-price")

        assert r.status_code == 200
        body = r.json()
        assert body["ticker"] == "005930"
        assert body["company_name"] == "삼성전자"
        assert body["current_price"] == 268000.0
        assert body["change_amount"] == -500.0
        assert body["change_pct"] == -0.19
        assert body["is_realtime"] is True
        assert body["source"] == "naver"
        assert body["corrected_from"] is None
        assert "queried_at" in body and body["queried_at"]

    def test_fallback_flags_are_exposed(self, monkeypatch):
        mock_resolution(monkeypatch, Resolution(company=COMPANY))
        mock_price_data(monkeypatch, FALLBACK_DATA)

        r = client.get("/api/stocks/005930/realtime-price")

        assert r.status_code == 200
        body = r.json()
        assert body["is_realtime"] is False
        assert body["source"] == "fallback"
        assert body["change_amount"] is None

    def test_corrected_from_is_echoed_when_alias_used(self, monkeypatch):
        mock_resolution(monkeypatch, Resolution(company=COMPANY, corrected_from="삼전"))
        mock_price_data(monkeypatch, REALTIME_DATA)

        r = client.get("/api/stocks/삼전/realtime-price")

        assert r.status_code == 200
        assert r.json()["corrected_from"] == "삼전"

    def test_unknown_company_is_404(self, monkeypatch):
        mock_resolution(monkeypatch, Resolution(message="'없는종목' 종목을 찾지 못했습니다."))
        called = []
        monkeypatch.setattr(
            stocks_api, "get_realtime_price_data", lambda db, company: called.append(1)
        )

        r = client.get("/api/stocks/없는종목/realtime-price")

        assert r.status_code == 404
        assert "없는종목" in r.json()["detail"]
        assert called == []  # 종목을 못 찾으면 시세 조회 자체를 시도하지 않는다

    def test_both_sources_unavailable_is_503(self, monkeypatch):
        mock_resolution(monkeypatch, Resolution(company=COMPANY))
        mock_price_data(monkeypatch, None)

        r = client.get("/api/stocks/005930/realtime-price")

        assert r.status_code == 503
        assert "삼성전자" in r.json()["detail"]
