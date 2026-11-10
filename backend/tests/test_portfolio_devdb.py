"""place_order/get_portfolio/get_or_create_account의 DB 오케스트레이션을 실제 dev DB로 검증한다.

핵심 계산(평균단가/잔고/수량)은 tests/test_portfolio.py에서 DB 없이 이미 검증했으므로, 여기서는
"그 결과가 실제로 Holding/VirtualAccount/Trade row에 바르게 반영되는지"만 본다. 가격은 실제 시세를
그대로 쓰면 장중/장외에 따라 값이 달라져 테스트가 불안정해지므로 get_realtime_price_data를 고정값으로
모킹한다(기존 dev_db 테스트들처럼 DB가 꺼져 있으면 skip).
"""

import pytest

import app.portfolio as portfolio_module
from app.db.session import SessionLocal
from app.models import VirtualAccount
from app.models.virtual_account import INITIAL_BALANCE
from app.portfolio import (
    InsufficientBalance,
    InsufficientQuantity,
    PriceUnavailable,
    get_or_create_account,
    get_portfolio,
    place_order,
)
from app.tools.company_resolver import resolve_company

TEST_DEVICE = "pytest-portfolio-devdb"


def fake_price(current_price: float, is_realtime: bool = True):
    def _fake(db, company):
        return {
            "current_price": current_price,
            "change_amount": 0.0,
            "change_pct": 0.0,
            "as_of": "t",
            "is_realtime": is_realtime,
            "source": "naver" if is_realtime else "fallback",
        }

    return _fake


@pytest.fixture
def trading_db(dev_db):
    """place_order 등을 라우터와 똑같이 별도 SessionLocal()로 호출하고, 끝나면 테스트 계좌를 지운다."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
        dev_db.rollback()
        account = dev_db.get(VirtualAccount, TEST_DEVICE)
        if account is not None:
            dev_db.delete(account)
            dev_db.commit()


@pytest.mark.integration
class TestPlaceOrderDevDB:
    def test_first_call_creates_account_with_initial_balance(self, trading_db):
        account = get_or_create_account(trading_db, TEST_DEVICE)
        assert float(account.cash_balance) == INITIAL_BALANCE

    def test_second_call_reuses_existing_account(self, trading_db):
        first = get_or_create_account(trading_db, TEST_DEVICE)
        first.cash_balance = 1_234
        trading_db.commit()
        second = get_or_create_account(trading_db, TEST_DEVICE)
        assert float(second.cash_balance) == 1_234

    def test_buy_then_additional_buy_then_full_sell(self, trading_db, monkeypatch):
        monkeypatch.setattr(portfolio_module, "get_realtime_price_data", fake_price(100_000))
        company = resolve_company(trading_db, "삼성전자").company

        bought = place_order(trading_db, TEST_DEVICE, company, "buy", 10)
        assert bought["holding"] == {"quantity": 10, "avg_price": 100_000}
        assert bought["cash_balance"] == INITIAL_BALANCE - 1_000_000

        bought_again = place_order(trading_db, TEST_DEVICE, company, "buy", 10)
        assert bought_again["holding"] == {"quantity": 20, "avg_price": 100_000}

        sold = place_order(trading_db, TEST_DEVICE, company, "sell", 20)
        assert sold["holding"] is None
        assert sold["cash_balance"] == INITIAL_BALANCE

        # 전량 매도 후 재매수는 과거 평균단가에 영향받지 않고 새 가격으로 시작한다
        monkeypatch.setattr(portfolio_module, "get_realtime_price_data", fake_price(150_000))
        rebought = place_order(trading_db, TEST_DEVICE, company, "buy", 1)
        assert rebought["holding"] == {"quantity": 1, "avg_price": 150_000}

    def test_insufficient_balance_is_raised(self, trading_db, monkeypatch):
        monkeypatch.setattr(portfolio_module, "get_realtime_price_data", fake_price(100_000))
        company = resolve_company(trading_db, "삼성전자").company
        with pytest.raises(InsufficientBalance):
            place_order(trading_db, TEST_DEVICE, company, "buy", 10_000)  # 10억원어치, 잔고 초과

    def test_insufficient_quantity_is_raised(self, trading_db, monkeypatch):
        monkeypatch.setattr(portfolio_module, "get_realtime_price_data", fake_price(100_000))
        company = resolve_company(trading_db, "삼성전자").company
        with pytest.raises(InsufficientQuantity):
            place_order(trading_db, TEST_DEVICE, company, "sell", 1)  # 보유 없음

    def test_price_unavailable_is_raised(self, trading_db, monkeypatch):
        monkeypatch.setattr(portfolio_module, "get_realtime_price_data", lambda db, company: None)
        company = resolve_company(trading_db, "삼성전자").company
        with pytest.raises(PriceUnavailable):
            place_order(trading_db, TEST_DEVICE, company, "buy", 1)


@pytest.mark.integration
class TestGetPortfolioDevDB:
    def test_shows_holding_with_eval_and_profit_loss(self, trading_db, monkeypatch):
        monkeypatch.setattr(portfolio_module, "get_realtime_price_data", fake_price(120_000))
        company = resolve_company(trading_db, "삼성전자").company
        place_order(trading_db, TEST_DEVICE, company, "buy", 10)

        monkeypatch.setattr(portfolio_module, "get_realtime_price_data", fake_price(150_000))
        p = get_portfolio(trading_db, TEST_DEVICE)

        assert len(p["holdings"]) == 1
        h = p["holdings"][0]
        assert (h["avg_price"], h["current_price"], h["quantity"]) == (120_000, 150_000, 10)
        assert h["eval_amount"] == 1_500_000
        assert h["profit_loss"] == 300_000  # (150,000 - 120,000) * 10
        assert h["profit_loss_pct"] == 25.0
        assert p["total_asset"] == p["cash_balance"] + h["eval_amount"]
        assert "note" not in p or p["note"] is None

    def test_empty_portfolio_has_zero_eval_and_no_holdings(self, trading_db):
        p = get_portfolio(trading_db, TEST_DEVICE)
        assert p == {
            "cash_balance": float(INITIAL_BALANCE),
            "holdings": [],
            "total_eval_amount": 0.0,
            "total_asset": float(INITIAL_BALANCE),
        }

    def test_unpriced_holding_is_excluded_from_total_and_noted(self, trading_db, monkeypatch):
        monkeypatch.setattr(portfolio_module, "get_realtime_price_data", fake_price(100_000))
        company = resolve_company(trading_db, "삼성전자").company
        place_order(trading_db, TEST_DEVICE, company, "buy", 1)

        monkeypatch.setattr(portfolio_module, "get_realtime_price_data", lambda db, c: None)
        p = get_portfolio(trading_db, TEST_DEVICE)

        assert p["holdings"][0]["current_price"] is None
        assert p["holdings"][0]["eval_amount"] is None
        assert p["total_eval_amount"] == 0.0
        assert p["total_asset"] == p["cash_balance"]
        assert "삼성전자" in p["note"]
