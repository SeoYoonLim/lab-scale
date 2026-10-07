"""GET /api/portfolio/trades, POST /api/portfolio/reset 라우트(DB 없이 mock). 실제 DB 동작은 test_portfolio_history_devdb.py."""

import pytest
from fastapi.testclient import TestClient

import app.api.portfolio as portfolio_api
from app.main import app
from app.tools.company_resolver import Resolution

client = TestClient(app, raise_server_exceptions=False)

USER_ID = 7
OWNER_KEY = f"user:{USER_ID}"
COMPANY = type("C", (), {"id": 1, "ticker": "005930", "name": "삼성전자"})()

TRADE = {
    "id": 3, "ticker": "005930", "company_name": "삼성전자", "side": "buy", "quantity": 5,
    "price": 270000.0, "amount": 1350000.0, "executed_at": "2026-10-07T01:00:00+00:00",
}
EMPTY_PORTFOLIO = {
    "cash_balance": 10_000_000.0, "holdings": [], "total_eval_amount": 0.0, "total_asset": 10_000_000.0,
}


@pytest.fixture(autouse=True)
def _logged_in(login_as):
    login_as(USER_ID)


@pytest.fixture
def no_db(monkeypatch):
    monkeypatch.setattr(portfolio_api, "SessionLocal", lambda: type("S", (), {"close": lambda self: None})())


class TestTradesAuth:
    @pytest.fixture(autouse=True)
    def _logged_out(self, _logged_in):
        app.dependency_overrides.clear()

    def test_no_token_is_401(self):
        r = client.get("/api/portfolio/trades")
        assert r.status_code == 401 and r.headers["www-authenticate"] == "Bearer"

    def test_reset_no_token_is_401(self):
        assert client.post("/api/portfolio/reset", json={"confirm": True}).status_code == 401


class TestTrades:
    def test_defaults_and_envelope(self, monkeypatch, no_db):
        seen = {}

        def fake(db, key, limit, offset, company_id=None):
            seen.update(key=key, limit=limit, offset=offset, company_id=company_id)
            return {"total": 1, "limit": limit, "offset": offset, "items": [TRADE]}

        monkeypatch.setattr(portfolio_api, "list_trades", fake)
        r = client.get("/api/portfolio/trades")
        assert r.status_code == 200
        assert r.json() == {"total": 1, "limit": 50, "offset": 0, "items": [TRADE]}
        assert seen == {"key": OWNER_KEY, "limit": 50, "offset": 0, "company_id": None}

    def test_empty_is_200(self, monkeypatch, no_db):
        monkeypatch.setattr(
            portfolio_api, "list_trades",
            lambda db, key, limit, offset, company_id=None: {"total": 0, "limit": limit, "offset": offset, "items": []},
        )
        r = client.get("/api/portfolio/trades")
        assert r.status_code == 200 and r.json() == {"total": 0, "limit": 50, "offset": 0, "items": []}

    def test_limit_boundaries_accepted(self, monkeypatch, no_db):
        monkeypatch.setattr(
            portfolio_api, "list_trades",
            lambda db, key, limit, offset, company_id=None: {"total": 0, "limit": limit, "offset": offset, "items": []},
        )
        assert client.get("/api/portfolio/trades?limit=1").json()["limit"] == 1
        assert client.get("/api/portfolio/trades?limit=200&offset=10").json()["limit"] == 200

    @pytest.mark.parametrize("query", ["limit=0", "limit=201", "limit=-1", "limit=abc", "offset=-1", "ticker="])
    def test_out_of_range_is_422(self, monkeypatch, query):
        monkeypatch.setattr(portfolio_api, "list_trades", lambda *a, **k: pytest.fail("호출되면 안 됨"))
        assert client.get(f"/api/portfolio/trades?{query}").status_code == 422

    def test_ticker_filter_resolves_company(self, monkeypatch, no_db):
        seen = {}
        monkeypatch.setattr(portfolio_api, "resolve_company", lambda db, t: Resolution(company=COMPANY))

        def fake(db, key, limit, offset, company_id=None):
            seen["company_id"] = company_id
            return {"total": 0, "limit": limit, "offset": offset, "items": []}

        monkeypatch.setattr(portfolio_api, "list_trades", fake)
        assert client.get("/api/portfolio/trades?ticker=삼성전자").status_code == 200
        assert seen["company_id"] == 1

    def test_unknown_ticker_is_404(self, monkeypatch, no_db):
        monkeypatch.setattr(
            portfolio_api, "resolve_company", lambda db, t: Resolution(message="'없는종목' 종목을 찾지 못했습니다.")
        )
        monkeypatch.setattr(portfolio_api, "list_trades", lambda *a, **k: pytest.fail("호출되면 안 됨"))
        assert client.get("/api/portfolio/trades?ticker=없는종목").status_code == 404

    def test_db_error_is_503(self, monkeypatch, no_db):
        from sqlalchemy.exc import OperationalError

        def boom(*a, **k):
            raise OperationalError("SELECT", {}, Exception("connection refused"))

        monkeypatch.setattr(portfolio_api, "list_trades", boom)
        r = client.get("/api/portfolio/trades")
        assert r.status_code == 503 and "데이터베이스" in r.json()["detail"]


class TestReset:
    @pytest.mark.parametrize(
        "kwargs",
        [
            {},  # 본문 없음
            {"json": {}},
            {"json": {"confirm": False}},
            {"json": {"confirm": "true"}},
            {"json": {"confirm": 1}},
            {"json": {"confirm": None}},
            {"json": {"other": True}},
            {"json": [True]},
            {"json": True},
        ],
    )
    def test_without_confirm_true_is_400_and_nothing_runs(self, monkeypatch, no_db, kwargs):
        monkeypatch.setattr(portfolio_api, "reset_portfolio", lambda *a, **k: pytest.fail("호출되면 안 됨"))
        r = client.post("/api/portfolio/reset", **kwargs)
        assert r.status_code == 400
        assert "confirm" in r.json()["detail"]

    def test_confirm_true_resets_own_account_and_returns_portfolio(self, monkeypatch, no_db):
        seen = []
        monkeypatch.setattr(portfolio_api, "reset_portfolio", lambda db, key: seen.append(("reset", key)))
        monkeypatch.setattr(
            portfolio_api, "get_portfolio", lambda db, key: seen.append(("get", key)) or EMPTY_PORTFOLIO
        )
        r = client.post("/api/portfolio/reset", json={"confirm": True})
        assert r.status_code == 200
        assert r.json() == {**EMPTY_PORTFOLIO, "note": None}  # GET /api/portfolio와 같은 형태
        assert seen == [("reset", OWNER_KEY), ("get", OWNER_KEY)]

    def test_failure_is_not_swallowed(self, monkeypatch, no_db):
        def boom(db, key):
            raise RuntimeError("fail")

        monkeypatch.setattr(portfolio_api, "reset_portfolio", boom)
        assert client.post("/api/portfolio/reset", json={"confirm": True}).status_code == 500
