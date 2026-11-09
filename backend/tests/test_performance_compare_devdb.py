"""성과·벤치마크 비교(view=benchmark)를 실제 DB로 검증한다: 인증, 사용자 분리, 이력 대조(현금 불일치 → history_unavailable),
기본 응답 불변, 고정 쿼리 수, 언어별 숫자 일치, 독립 재계산.

체결은 실제 주문 경로(POST /api/portfolio/orders)로 만들고 시세 조회(외부)는 가짜 가격으로 바꾼다. 테스트 계정은 captest_이며 signup 픽스처가
지운다(계좌를 지우면 체결·보유도 CASCADE). CI의 빈 DB(시세 없음)에서도 맞게: 시세가 없으면 no_prices를 기대한다.
"""

import secrets
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, text

import app.portfolio as portfolio_module
from app.db.session import SessionLocal, engine
from app.main import app
from app.models import VirtualAccount
from app.performance_db import get_performance_view

client = TestClient(app, raise_server_exceptions=False)
pytestmark = pytest.mark.integration
URL = "/api/portfolio/performance"
PRICE = 1000.0


@pytest.fixture
def captest(signup):
    return lambda: signup(f"captest_{secrets.token_hex(5)}")


@pytest.fixture(autouse=True)
def fixed_price(monkeypatch):
    monkeypatch.setattr(portfolio_module, "get_realtime_price_data",
                        lambda db, c: {"current_price": PRICE, "change_amount": 0.0, "change_pct": 0.0, "as_of": "t", "is_realtime": True, "source": "x"})


@pytest.fixture
def ticker(dev_db):
    return dev_db.execute(text("SELECT ticker FROM company WHERE asset_type = 'stock' ORDER BY id LIMIT 1")).scalar()


def order(user, ticker, side, qty):
    r = client.post("/api/portfolio/orders", json={"ticker": ticker, "side": side, "quantity": qty}, headers=user["headers"])
    assert r.status_code == 200, r.text


def view(user, **params):
    return client.get(URL, params={"view": "benchmark", **params}, headers=user["headers"])


def has_prices(dev_db) -> bool:
    return dev_db.execute(text("SELECT EXISTS (SELECT 1 FROM stock_price)")).scalar()


def test_requires_login():
    assert client.get(URL, params={"view": "benchmark"}).status_code == 401


@pytest.mark.parametrize("q", [{"view": "nope"}, {"view": "benchmark", "benchmark": "KS200"}, {"period": "2y"}])
def test_bad_params_are_422(captest, q):
    assert client.get(URL, params=q, headers=captest()["headers"]).status_code == 422


def test_no_account_and_default_view_unchanged(captest):
    user = captest()
    body = view(user).json()
    assert body["status"] == "no_account" and body["series"]["points"] == [] and body["comparison"]["status"] == "portfolio_unavailable"
    default = client.get(URL, headers=user["headers"]).json()
    assert set(default) == {"generated_at", "period", "data_date", "stale", "stale_notice", "start_date", "end_date", "initial_balance", "message",
                            "summary", "risk", "trades", "contributions", "benchmark", "curve", "data_gaps", "notices", "disclaimer"}


def test_trades_are_replayed_and_users_isolated(captest, dev_db, ticker):
    a, b = captest(), captest()
    order(a, ticker, "buy", 10)
    order(a, ticker, "sell", 4)
    mine, theirs = view(a).json(), view(b).json()
    assert theirs["status"] == "no_account" and ticker not in str(theirs)
    if not has_prices(dev_db):
        assert mine["status"] == "no_prices"
        return
    # 오늘 체결은 아직 종가가 없다(마지막 종가일 뒤) → pending_only. 이력 대조는 통과(현금·보유가 체결 재생과 같다)
    assert mine["status"] == "pending_only" and mine["history_verified"] is True
    assert mine["summary"]["current_cash"] == 10_000_000 - 10 * PRICE + 4 * PRICE


def test_cash_mismatch_is_history_unavailable(captest, dev_db, ticker):
    user = captest()
    order(user, ticker, "buy", 1)
    dev_db.query(VirtualAccount).filter(VirtualAccount.device_id == f"user:{user['id']}").update({"cash_balance": 123})
    dev_db.commit()
    body = view(user).json()
    # 이력 불일치는 시세가 있어도 계산할 수 없는 문제라 시세 유무(CI의 빈 DB)와 관계없이 history_unavailable이 우선이다
    assert body["status"] == "history_unavailable" and body["history_verified"] is False and body["series"]["points"] == []
    assert any(w["kind"] == "history_mismatch" and "cash" in w["parts"] for w in body["warnings"])


def test_backdated_trades_match_independent_sql(captest, dev_db, ticker):
    """과거 체결(executed_at을 과거로 옮긴 실제 체결 행)로 날짜별 평가를 SQL 종가로 따로 계산해 대조한다(시세가 있을 때만 값 비교)."""
    user = captest()
    order(user, ticker, "buy", 10)
    if not has_prices(dev_db):
        assert view(user).json()["status"] == "no_prices"
        return
    days = [d for (d,) in dev_db.execute(text(
        "SELECT p.price_date FROM stock_price p JOIN company c ON c.id = p.company_id WHERE c.ticker = :t ORDER BY p.price_date DESC LIMIT 6"),
        {"t": ticker})][::-1]
    trade_day = days[1]
    dev_db.execute(text("UPDATE trade SET executed_at = (:d)::date + time '10:00' AT TIME ZONE 'Asia/Seoul' WHERE device_id = :k"),
                   {"d": trade_day, "k": f"user:{user['id']}"})
    dev_db.commit()
    body = view(user, period="1m").json()
    assert body["status"] in ("ok", "partial") and body["history_verified"] is True
    closes = dict(dev_db.execute(text("SELECT p.price_date, p.close_price FROM stock_price p JOIN company c ON c.id = p.company_id "
                                      "WHERE c.ticker = :t AND p.price_date >= :d"), {"t": ticker, "d": trade_day}).all())
    cash = 10_000_000 - 10 * PRICE
    for p in body["series"]["points"]:
        if p["date"] >= trade_day.isoformat() and not p["estimated"]:
            assert p["total_asset"] == round(cash + 10 * float(closes[date.fromisoformat(p["date"])]), 2), p
    c = body["comparison"]
    if c["status"] in ("ok", "partial"):
        assert c["excess_return_pp"] == round(c["portfolio_return_pct"] - c["benchmark_return_pct"], 2)


def test_locales_keep_numbers(captest, ticker):
    user = captest()
    order(user, ticker, "buy", 3)
    bodies = {L: view(user, locale=L, benchmark="KOSDAQ").json() for L in ("ko-KR", "en-US", "ja-JP", "zh-CN")}
    ko = bodies["ko-KR"]
    for L, b in bodies.items():
        assert b["locale"] == L and b["benchmark_code"] == "KQ11"
        for k in ("status", "summary", "metrics", "series", "data_date", "estimated_dates"):
            assert b[k] == ko[k], (L, k)
        assert [w["kind"] for w in b["warnings"]] == [w["kind"] for w in ko["warnings"]]


def test_query_count_is_constant(captest, ticker):
    small, big = captest(), captest()
    order(small, ticker, "buy", 1)
    for _ in range(5):
        order(big, ticker, "buy", 1)

    def count(user):
        seen = []
        listener = lambda *a: seen.append(1)  # noqa: E731
        event.listen(engine, "before_cursor_execute", listener)
        try:
            db = SessionLocal()
            try:
                get_performance_view(db, f"user:{user['id']}", "all", "KQ11")
            finally:
                db.close()
        finally:
            event.remove(engine, "before_cursor_execute", listener)
        return len(seen)

    assert count(small) == count(big) <= 7
