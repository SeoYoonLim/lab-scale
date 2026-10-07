"""체결 내역 조회와 포트폴리오 초기화를 실제 dev DB + 실제 토큰으로 검증한다. 만든 사용자는 끝나면 지운다."""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import app.portfolio as portfolio_module
from app.db.session import SessionLocal
from app.main import app
from app.models import Company, Holding, Trade, VirtualAccount, Watchlist
from app.models.virtual_account import INITIAL_BALANCE
from app.portfolio import get_or_create_account, list_trades, reset_portfolio

client = TestClient(app, raise_server_exceptions=False)

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def fixed_price(monkeypatch):
    """실제 시세 대신 고정 가격으로 체결한다(장중/장외에 따라 값이 달라 테스트가 흔들리는 것을 막는다)."""
    monkeypatch.setattr(
        portfolio_module,
        "get_realtime_price_data",
        lambda db, company: {"current_price": 1000.0, "change_amount": 0.0, "change_pct": 0.0, "as_of": "t",
                             "is_realtime": True, "source": "naver"},
    )


def _order(user, ticker, side, quantity):
    r = client.post("/api/portfolio/orders", json={"ticker": ticker, "side": side, "quantity": quantity},
                    headers=user["headers"])
    assert r.status_code == 200, r.text
    return r.json()


def _trades(user, query=""):
    r = client.get(f"/api/portfolio/trades{query}", headers=user["headers"])
    assert r.status_code == 200, r.text
    return r.json()


def _counts(dev_db, owner_key):
    dev_db.rollback()
    return {
        "holdings": dev_db.query(Holding).filter(Holding.device_id == owner_key).count(),
        "trades": dev_db.query(Trade).filter(Trade.device_id == owner_key).count(),
        "cash": float(dev_db.get(VirtualAccount, owner_key).cash_balance),
    }


class TestTrades:
    def test_empty_is_200_with_zero_total(self, signup):
        user = signup()
        assert _trades(user) == {"total": 0, "limit": 50, "offset": 0, "items": []}

    def test_fields_and_amount_after_real_orders(self, signup):
        user = signup()
        _order(user, "005930", "buy", 3)
        _order(user, "005930", "sell", 1)
        body = _trades(user)
        assert body["total"] == 2
        sell, buy = body["items"]  # 최신순
        assert (sell["side"], sell["quantity"], sell["price"], sell["amount"]) == ("sell", 1, 1000.0, 1000.0)
        assert (buy["side"], buy["quantity"], buy["amount"]) == ("buy", 3, 3000.0)
        assert buy["ticker"] == "005930" and buy["company_name"] == "삼성전자"
        assert set(buy) == {"id", "ticker", "company_name", "side", "quantity", "price", "amount", "executed_at"}
        assert sell["id"] > buy["id"]

    def test_pagination_ticker_filter_and_ordering(self, signup, dev_db):
        user = signup()
        key = f"user:{user['id']}"
        db = SessionLocal()
        try:
            get_or_create_account(db, key)
            samsung = db.query(Company).filter(Company.ticker == "005930").one()
            hynix = db.query(Company).filter(Company.ticker == "000660").one()
            base = datetime(2026, 10, 1, tzinfo=timezone.utc)
            # 체결 시각이 같은 두 건(id가 큰 쪽이 먼저 나와야 한다) + 시간 순서가 id 순서와 다른 한 건
            rows = [
                Trade(device_id=key, company_id=samsung.id, side="buy", quantity=1, price=100, executed_at=base),
                Trade(device_id=key, company_id=hynix.id, side="buy", quantity=2, price=200, executed_at=base),
                Trade(device_id=key, company_id=samsung.id, side="sell", quantity=1, price=110,
                      executed_at=base - timedelta(days=1)),
                Trade(device_id=key, company_id=samsung.id, side="buy", quantity=4, price=120,
                      executed_at=base + timedelta(days=1)),
                Trade(device_id=key, company_id=hynix.id, side="sell", quantity=1, price=210,
                      executed_at=base + timedelta(days=2)),
            ]
            db.add_all(rows)
            db.commit()
            ids = [r.id for r in rows]
        finally:
            db.close()

        # 최신순: 시각 desc, 같으면 id desc
        expected = [ids[4], ids[3], ids[1], ids[0], ids[2]]
        assert [i["id"] for i in _trades(user)["items"]] == expected

        page1 = _trades(user, "?limit=2&offset=0")
        page2 = _trades(user, "?limit=2&offset=2")
        page3 = _trades(user, "?limit=2&offset=4")
        assert (page1["total"], page1["limit"], page1["offset"]) == (5, 2, 0)
        assert [i["id"] for i in page1["items"] + page2["items"] + page3["items"]] == expected
        assert len(page3["items"]) == 1
        assert _trades(user, "?limit=2&offset=99") == {"total": 5, "limit": 2, "offset": 99, "items": []}

        only_hynix = _trades(user, "?ticker=000660")
        assert only_hynix["total"] == 2 and {i["ticker"] for i in only_hynix["items"]} == {"000660"}
        assert [i["id"] for i in only_hynix["items"]] == [ids[4], ids[1]]
        assert _trades(user, "?ticker=SK하이닉스")["total"] == 2  # 종목명으로도 필터
        assert _trades(user, "?ticker=005930&limit=1")["total"] == 3

    def test_unknown_ticker_is_404(self, signup):
        user = signup()
        assert client.get("/api/portfolio/trades?ticker=존재하지않는종목XYZ", headers=user["headers"]).status_code == 404

    def test_other_users_trades_are_not_visible(self, signup):
        a, b = signup(), signup()
        _order(a, "005930", "buy", 1)
        _order(a, "000660", "buy", 2)
        _order(b, "005930", "buy", 5)
        assert _trades(a)["total"] == 2 and _trades(b)["total"] == 1
        assert [i["quantity"] for i in _trades(b)["items"]] == [5]

    def test_no_n_plus_one(self, signup):
        """체결 수가 늘어도 SELECT 수가 그대로여야 한다(company를 join으로 한 번에 읽는다)."""
        from sqlalchemy import event

        user = signup()
        for _ in range(4):
            _order(user, "005930", "buy", 1)
        _order(user, "000660", "buy", 1)

        db = SessionLocal()
        statements = []

        def record(conn, cursor, statement, *args):
            statements.append(statement)

        engine = db.get_bind()
        event.listen(engine, "before_cursor_execute", record)
        try:
            result = list_trades(db, f"user:{user['id']}", 50, 0)
        finally:
            event.remove(engine, "before_cursor_execute", record)
            db.close()
        assert len(result["items"]) == 5
        selects = [s for s in statements if s.lstrip().upper().startswith("SELECT") and "FROM trade" in s]
        assert len(selects) == 2  # count 1회 + 목록 1회


class TestReset:
    def test_reset_clears_own_data_and_restores_balance(self, signup, dev_db):
        user = signup()
        key = f"user:{user['id']}"
        _order(user, "005930", "buy", 3)
        _order(user, "000660", "buy", 2)
        assert _counts(dev_db, key) == {"holdings": 2, "trades": 2, "cash": INITIAL_BALANCE - 5000}

        r = client.post("/api/portfolio/reset", json={"confirm": True}, headers=user["headers"])
        assert r.status_code == 200
        body = r.json()
        assert body["cash_balance"] == INITIAL_BALANCE and body["holdings"] == []
        assert body["total_asset"] == INITIAL_BALANCE
        # GET /api/portfolio와 같은 응답이다
        assert client.get("/api/portfolio", headers=user["headers"]).json() == body

        assert _counts(dev_db, key) == {"holdings": 0, "trades": 0, "cash": INITIAL_BALANCE}
        assert _trades(user) == {"total": 0, "limit": 50, "offset": 0, "items": []}
        # 초기화 뒤에도 정상적으로 다시 거래할 수 있다(전량 매도 후 재매수처럼 평균단가가 새로 시작)
        assert _order(user, "005930", "buy", 1)["holding"] == {"quantity": 1, "avg_price": 1000.0}

    def test_reset_without_confirm_changes_nothing(self, signup, dev_db):
        user = signup()
        key = f"user:{user['id']}"
        _order(user, "005930", "buy", 3)
        before = _counts(dev_db, key)
        for kwargs in ({}, {"json": {"confirm": False}}, {"json": {}}):
            r = client.post("/api/portfolio/reset", headers=user["headers"], **kwargs)
            assert r.status_code == 400
        assert _counts(dev_db, key) == before

    def test_reset_first_call_creates_account(self, signup, dev_db):
        user = signup()
        r = client.post("/api/portfolio/reset", json={"confirm": True}, headers=user["headers"])
        assert r.status_code == 200 and r.json()["cash_balance"] == INITIAL_BALANCE
        assert _counts(dev_db, f"user:{user['id']}") == {"holdings": 0, "trades": 0, "cash": INITIAL_BALANCE}

    def test_reset_does_not_touch_other_users(self, signup, dev_db):
        a, b = signup(), signup()
        _order(a, "005930", "buy", 3)
        _order(b, "005930", "buy", 5)
        _order(b, "000660", "buy", 1)
        before_b = _counts(dev_db, f"user:{b['id']}")
        b_trades_before = _trades(b)
        b_portfolio_before = client.get("/api/portfolio", headers=b["headers"]).json()

        assert client.post("/api/portfolio/reset", json={"confirm": True}, headers=a["headers"]).status_code == 200

        assert _counts(dev_db, f"user:{a['id']}") == {"holdings": 0, "trades": 0, "cash": INITIAL_BALANCE}
        assert _counts(dev_db, f"user:{b['id']}") == before_b
        assert _trades(b) == b_trades_before
        assert client.get("/api/portfolio", headers=b["headers"]).json() == b_portfolio_before

    def test_reset_keeps_watchlist_and_research_reports(self, signup, dev_db):
        from app.reports import get_report, save_report

        user = signup()
        key = f"user:{user['id']}"
        assert client.post("/api/watchlist", json={"ticker": "005930"}, headers=user["headers"]).status_code == 201
        report_id = save_report("[pytest] reset 유지 확인", "답변", [], user_id=user["id"])
        _order(user, "005930", "buy", 1)

        assert client.post("/api/portfolio/reset", json={"confirm": True}, headers=user["headers"]).status_code == 200

        dev_db.rollback()
        assert dev_db.query(Watchlist).filter(Watchlist.device_id == key).count() == 1
        assert [i["ticker"] for i in client.get("/api/watchlist", headers=user["headers"]).json()["items"]] == ["005930"]
        assert get_report(report_id, user_id=user["id"]) is not None
        assert client.get(f"/api/research/{report_id}", headers=user["headers"]).status_code == 200

    def test_failure_rolls_everything_back(self, signup, dev_db, monkeypatch):
        """삭제까지 끝낸 뒤 커밋에서 실패하면 보유/체결/잔고 모두 원래대로 남아야 한다."""
        user = signup()
        key = f"user:{user['id']}"
        _order(user, "005930", "buy", 3)
        _order(user, "000660", "buy", 2)
        before = _counts(dev_db, key)
        assert before["holdings"] == 2 and before["trades"] == 2

        db = SessionLocal()
        try:
            def boom():
                raise RuntimeError("commit 실패 시뮬레이션")

            monkeypatch.setattr(db, "commit", boom)
            with pytest.raises(RuntimeError, match="시뮬레이션"):
                reset_portfolio(db, key)
        finally:
            db.close()

        assert _counts(dev_db, key) == before
        assert _trades(user)["total"] == 2
