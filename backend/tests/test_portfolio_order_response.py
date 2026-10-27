"""place_order의 응답은 커밋 "전"(계좌 행 잠금이 걸려 있는 동안)에 확정한다.

배경: 커밋하면 계좌 행 잠금이 풀린다. 그 직후 겹친 reset_portfolio가 보유·거래 행을 지우면, 커밋 뒤에 db.refresh(holding)이나
만료된 속성(trade.executed_at) 접근이 InvalidRequestError("Could not refresh instance")를 내서 이미 체결된 주문이 오류로 끝났다.
GitHub Actions의 동시성 통합 테스트(test_order_and_reset_overlap_stays_consistent)에서 처음 드러났다(느린 러너에서만).
실제 DB 경쟁은 tests/test_portfolio_concurrency_devdb.py가 보고, 여기서는 "커밋 이후에는 ORM 객체를 새로고침하거나 읽지 않는다"는 구조를
가짜 세션으로 DB 없이 고정한다.
"""

from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

import app.portfolio as portfolio
from app.models import Trade


class Refreshed(Exception):
    pass


class FakeSession:
    """커밋 뒤에 refresh하거나 만료된 객체를 읽으면 실패하는 세션(= 잠금이 풀린 뒤 행이 지워졌을 때와 같은 상황)."""

    def __init__(self, holding):
        self.holding = holding
        self.committed = False
        self.deleted = []
        self.added = []

    def query(self, *a):
        return self

    def filter(self, *a, **k):
        return self

    def populate_existing(self):
        return self

    def first(self):
        return self.holding

    def add(self, obj):
        self.added.append(obj)

    def delete(self, obj):
        self.deleted.append(obj)

    def flush(self):
        assert not self.committed

    def refresh(self, obj):
        if self.committed:
            raise Refreshed("커밋 뒤 refresh: 그 사이 초기화가 행을 지웠다면 InvalidRequestError")
        if isinstance(obj, Trade) and obj.executed_at is None:
            obj.executed_at = datetime(2026, 10, 27, 9, 30, tzinfo=timezone.utc)

    def commit(self):
        self.committed = True

    def rollback(self):
        pass


@pytest.fixture
def setup(monkeypatch):
    account = SimpleNamespace(cash_balance=Decimal("10000000"))
    monkeypatch.setattr(portfolio, "get_realtime_price_data", lambda db, c: {"current_price": 10000.0})
    monkeypatch.setattr(portfolio, "_lock_account", lambda db, key: account)
    company = SimpleNamespace(id=1, ticker="005930", name="삼성전자")
    return account, company


def test_buy_response_is_built_before_the_commit(setup):
    account, company = setup
    db = FakeSession(SimpleNamespace(quantity=10, avg_price=Decimal("10000")))
    out = portfolio.place_order(db, "owner", company, "buy", 5)  # 커밋 뒤 refresh/접근이 있으면 Refreshed
    assert db.committed
    assert out["holding"] == {"quantity": 15, "avg_price": 10000.0}
    assert out["cash_balance"] == 10_000_000 - 5 * 10_000
    assert out["executed_at"] == "2026-10-27T09:30:00+00:00" and out["side"] == "buy" and out["price"] == 10000.0


def test_new_holding_buy_and_full_sell(setup):
    account, company = setup
    db = FakeSession(None)  # 보유 없음 -> 새로 만든다
    out = portfolio.place_order(db, "owner", company, "buy", 3)
    assert out["holding"]["quantity"] == 3 and len(db.added) == 2  # Holding + Trade

    db = FakeSession(SimpleNamespace(quantity=4, avg_price=Decimal("9000")))
    out = portfolio.place_order(db, "owner", company, "sell", 4)  # 전량 매도: 보유 행 삭제
    assert out["holding"] is None and db.deleted and db.committed


def test_refresh_failure_before_commit_rolls_back_instead_of_half_committing(setup):
    account, company = setup

    class FailingSession(FakeSession):
        rolled_back = False

        def refresh(self, obj):
            raise RuntimeError("db hiccup")

        def rollback(self):
            self.rolled_back = True

    db = FailingSession(SimpleNamespace(quantity=1, avg_price=Decimal("1")))
    with pytest.raises(RuntimeError):
        portfolio.place_order(db, "owner", company, "buy", 1)
    assert db.rolled_back and not db.committed  # 응답을 못 만들면 주문도 확정하지 않는다
