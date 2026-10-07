import pytest
from fastapi.testclient import TestClient

import app.api.portfolio as portfolio_api
from app.main import app
from app.portfolio import InsufficientBalance, InsufficientQuantity, PriceUnavailable
from app.tools.company_resolver import Resolution

client = TestClient(app, raise_server_exceptions=False)

USER_ID = 7
# 예전에는 X-Device-Id를 보냈다. 이제 인증은 get_current_user가 하고(아래 autouse 픽스처로 가짜 사용자), 헤더는 필요 없다.
HEADERS = {}
COMPANY = type("C", (), {"id": 1, "ticker": "005930", "name": "삼성전자"})()

PORTFOLIO = {
    "cash_balance": 8_650_000.0,
    "holdings": [
        {
            "ticker": "005930", "company_name": "삼성전자", "quantity": 5, "avg_price": 270000.0,
            "current_price": 270000.0, "is_realtime": True, "eval_amount": 1350000.0,
            "profit_loss": 0.0, "profit_loss_pct": 0.0,
        }
    ],
    "total_eval_amount": 1350000.0,
    "total_asset": 10000000.0,
}

ORDER_RESULT = {
    "ticker": "005930", "company_name": "삼성전자", "side": "buy", "quantity": 5, "price": 270000.0,
    "executed_at": "2026-10-01T00:00:00+00:00", "cash_balance": 8_650_000.0,
    "holding": {"quantity": 5, "avg_price": 270000.0},
}


@pytest.fixture(autouse=True)
def _logged_in(login_as):
    login_as(USER_ID)


def mock_resolution(monkeypatch, res):
    monkeypatch.setattr(portfolio_api, "resolve_company", lambda db, ticker: res)


class TestAuthRequired:
    @pytest.fixture(autouse=True)
    def _logged_out(self, _logged_in):
        app.dependency_overrides.clear()

    def test_missing_token_is_401(self):
        assert client.get("/api/portfolio").status_code == 401
        assert client.post("/api/portfolio/orders", json={"ticker": "005930", "side": "buy", "quantity": 1}).status_code == 401

    def test_device_id_header_is_no_longer_accepted(self):
        assert client.get("/api/portfolio", headers={"X-Device-Id": "test-device-1"}).status_code == 401


class TestGetPortfolio:
    def test_returns_service_result(self, monkeypatch):
        monkeypatch.setattr(portfolio_api, "get_portfolio", lambda db, device_id: PORTFOLIO)
        r = client.get("/api/portfolio", headers=HEADERS)
        assert r.status_code == 200
        body = r.json()
        assert body["cash_balance"] == 8_650_000.0
        assert body["holdings"][0]["ticker"] == "005930"
        assert body.get("note") is None

    def test_passes_user_owner_key(self, monkeypatch):
        seen = {}
        monkeypatch.setattr(
            portfolio_api, "get_portfolio",
            lambda db, device_id: seen.setdefault("id", device_id) or {**PORTFOLIO, "holdings": []},
        )
        client.get("/api/portfolio", headers={"X-Device-Id": "device-xyz"})  # 헤더는 무시된다
        assert seen["id"] == f"user:{USER_ID}"

    def test_order_uses_user_owner_key(self, monkeypatch):
        seen = []
        mock_resolution(monkeypatch, Resolution(company=COMPANY))
        monkeypatch.setattr(portfolio_api, "place_order", lambda db, key, c, s, q: seen.append(key) or ORDER_RESULT)
        client.post("/api/portfolio/orders", json={"ticker": "005930", "side": "buy", "quantity": 5})
        assert seen == [f"user:{USER_ID}"]


class TestCreateOrder:
    def _order(self, **overrides):
        body = {"ticker": "005930", "side": "buy", "quantity": 5}
        body.update(overrides)
        return client.post("/api/portfolio/orders", json=body, headers=HEADERS)

    def test_success(self, monkeypatch):
        mock_resolution(monkeypatch, Resolution(company=COMPANY))
        monkeypatch.setattr(portfolio_api, "place_order", lambda db, device_id, company, side, qty: ORDER_RESULT)

        r = self._order()

        assert r.status_code == 200
        assert r.json() == ORDER_RESULT

    def test_unknown_company_is_404_and_place_order_not_called(self, monkeypatch):
        mock_resolution(monkeypatch, Resolution(message="'없는종목' 종목을 찾지 못했습니다."))
        called = []
        monkeypatch.setattr(portfolio_api, "place_order", lambda *a, **k: called.append(1))

        r = self._order(ticker="없는종목")

        assert r.status_code == 404
        assert called == []

    def test_insufficient_balance_is_400(self, monkeypatch):
        mock_resolution(monkeypatch, Resolution(company=COMPANY))

        def boom(db, device_id, company, side, qty):
            raise InsufficientBalance("잔고가 부족합니다(필요 1,000원, 보유 500원).")

        monkeypatch.setattr(portfolio_api, "place_order", boom)
        r = self._order()
        assert r.status_code == 400
        assert "잔고" in r.json()["detail"]

    def test_insufficient_quantity_is_400(self, monkeypatch):
        mock_resolution(monkeypatch, Resolution(company=COMPANY))

        def boom(db, device_id, company, side, qty):
            raise InsufficientQuantity("보유 수량이 부족합니다(매도 요청 5주, 보유 0주).")

        monkeypatch.setattr(portfolio_api, "place_order", boom)
        r = self._order(side="sell")
        assert r.status_code == 400
        assert "보유 수량" in r.json()["detail"]

    def test_price_unavailable_is_503(self, monkeypatch):
        mock_resolution(monkeypatch, Resolution(company=COMPANY))

        def boom(db, device_id, company, side, qty):
            raise PriceUnavailable("'삼성전자'의 가격을 구하지 못해 주문을 체결할 수 없습니다.")

        monkeypatch.setattr(portfolio_api, "place_order", boom)
        r = self._order()
        assert r.status_code == 503

    def test_invalid_side_is_422(self):
        r = self._order(side="hold")
        assert r.status_code == 422

    @pytest.mark.parametrize("quantity", [0, -1, 100_001])
    def test_quantity_out_of_range_is_422(self, quantity):
        r = self._order(quantity=quantity)
        assert r.status_code == 422

    def test_blank_ticker_is_422(self):
        r = self._order(ticker="")
        assert r.status_code == 422
