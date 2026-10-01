"""매수/매도 핵심 로직(apply_buy/apply_sell) 단위 테스트. DB 없음.

DB를 실제로 건드리는 place_order/get_portfolio/get_or_create_account는
tests/test_portfolio_devdb.py에서 dev_db 통합 테스트로 검증한다.
"""

import pytest

from app.portfolio import InsufficientBalance, InsufficientQuantity, apply_buy, apply_sell


class TestApplyBuy:
    def test_new_position_avg_price_is_execution_price(self):
        r = apply_buy(cash_balance=1_000_000, quantity=10, price=50_000)
        assert r == {"cash_balance": 500_000, "quantity": 10, "avg_price": 50_000}

    def test_insufficient_balance_raises_and_leaves_nothing_applied(self):
        with pytest.raises(InsufficientBalance):
            apply_buy(cash_balance=100_000, quantity=10, price=50_000)

    def test_balance_exactly_equal_to_total_succeeds(self):
        r = apply_buy(cash_balance=500_000, quantity=10, price=50_000)
        assert r["cash_balance"] == 0

    def test_additional_buy_recomputes_weighted_average(self):
        # 기존 10주 @50,000 + 10주 @70,000 = 20주 평균 60,000
        r = apply_buy(cash_balance=1_000_000, quantity=10, price=70_000, holding_qty=10, holding_avg_price=50_000)
        assert r == {"cash_balance": 300_000, "quantity": 20, "avg_price": 60_000}

    def test_weighted_average_with_uneven_quantities_rounds_to_cents(self):
        # 3주 @10,000 + 7주 @13,333.33 = 10주 평균 (30000 + 93333.31) / 10 = 12333.33
        r = apply_buy(cash_balance=10_000_000, quantity=7, price=13_333.33, holding_qty=3, holding_avg_price=10_000)
        assert r["quantity"] == 10
        assert r["avg_price"] == pytest.approx(12333.33)

    def test_buying_after_full_sell_starts_a_fresh_average(self):
        # 전량 매도 후(holding_qty=0으로 취급) 다시 사면 과거 평균단가에 영향받지 않는다
        r = apply_buy(cash_balance=1_000_000, quantity=5, price=80_000, holding_qty=0, holding_avg_price=50_000)
        assert r["avg_price"] == 80_000


class TestApplySell:
    def test_insufficient_quantity_raises(self):
        with pytest.raises(InsufficientQuantity):
            apply_sell(cash_balance=0, quantity=10, price=50_000, holding_qty=5)

    def test_selling_more_than_zero_held_raises(self):
        with pytest.raises(InsufficientQuantity):
            apply_sell(cash_balance=0, quantity=1, price=50_000, holding_qty=0)

    def test_partial_sell_reduces_quantity_and_credits_cash(self):
        r = apply_sell(cash_balance=100_000, quantity=4, price=10_000, holding_qty=10)
        assert r == {"cash_balance": 140_000, "quantity": 6}

    def test_full_sell_leaves_quantity_at_zero(self):
        r = apply_sell(cash_balance=0, quantity=10, price=50_000, holding_qty=10)
        assert r["quantity"] == 0
        assert r["cash_balance"] == 500_000

    def test_sell_result_has_no_avg_price_key(self):
        # 평균단가는 매도로 바뀌지 않는다(호출측이 기존 값을 그대로 둔다) - 반환값에 포함하지 않아 실수로 덮어쓰지 못하게 한다
        r = apply_sell(cash_balance=0, quantity=1, price=10_000, holding_qty=5)
        assert "avg_price" not in r
