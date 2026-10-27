"""모의투자 동시 주문 보호(계좌 행 잠금)를 실제 스레드 + 스레드별 별도 DB 세션으로 검증한다.

각 스레드가 SessionLocal()을 따로 열어 place_order를 직접 호출한다(라우터와 같은 방식). 가격은
get_realtime_price_data를 고정값으로 monkeypatch해서 외부 호출 없이 돌린다. 고정 가격 함수 안에 Barrier를 둬서
모든 스레드가 가격 조회를 마친 시점(= 잠금 이전)에 동시에 출발하게 한다. 계좌 키는 `captest_` + 랜덤이고
끝나면 지운다(holding/trade는 virtual_account FK CASCADE).
"""

import secrets
import threading

import pytest

import app.portfolio as portfolio_module
from app.db.session import SessionLocal
from app.models import Company, Holding, Trade, VirtualAccount
from app.models.virtual_account import INITIAL_BALANCE
from app.portfolio import (
    InsufficientBalance,
    InsufficientQuantity,
    get_or_create_account,
    place_order,
    reset_portfolio,
)

PRICE = 10_000


@pytest.fixture
def owner(dev_db):
    key = f"captest_{secrets.token_hex(6)}"
    yield key
    dev_db.rollback()
    dev_db.query(VirtualAccount).filter(VirtualAccount.device_id == key).delete()
    dev_db.commit()


@pytest.fixture
def company(dev_db):
    c = dev_db.query(Company).filter(Company.ticker == "005930").first()
    if c is None:
        pytest.skip("dev DB에 삼성전자(005930)가 없음")
    dev_db.expunge(c)
    return c


def fix_price(monkeypatch, parties: int, price: float = PRICE):
    """가격 조회를 고정하고, parties개 스레드가 모두 여기 도착할 때까지 기다리게 한다(동시 출발)."""
    barrier = threading.Barrier(parties, timeout=20)

    def _fake(db, company):
        try:
            barrier.wait()
        except threading.BrokenBarrierError:
            pass
        return {
            "current_price": price,
            "change_amount": 0.0,
            "change_pct": 0.0,
            "as_of": "t",
            "is_realtime": True,
            "source": "fake",
        }

    monkeypatch.setattr(portfolio_module, "get_realtime_price_data", _fake)
    return barrier


def run_parallel(jobs):
    """jobs: 인자 없는 callable 목록. 각자 새 SessionLocal을 쓰도록 호출측이 구성한다. [(result|None, exc|None)]"""
    out = [None] * len(jobs)

    def _run(i, job):
        try:
            out[i] = (job(), None)
        except BaseException as e:  # noqa: BLE001 - 어떤 예외든 결과로 수집해서 단언한다
            out[i] = (None, e)

    threads = [threading.Thread(target=_run, args=(i, j)) for i, j in enumerate(jobs)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert all(r is not None for r in out), "스레드가 끝나지 않음(교착?)"
    return out


def order_job(owner, company, side, qty):
    def _job():
        db = SessionLocal()
        try:
            return place_order(db, owner, company, side, qty)
        finally:
            db.close()

    return _job


def snapshot(dev_db, owner):
    dev_db.rollback()  # 이전 읽기 스냅샷을 버리고 최신 상태를 본다
    account = dev_db.get(VirtualAccount, owner)
    if account is not None:
        dev_db.refresh(account)
    holdings = dev_db.query(Holding).filter(Holding.device_id == owner).all()
    trades = dev_db.query(Trade).filter(Trade.device_id == owner).all()
    dev_db.rollback()
    return account, holdings, trades


def set_balance(dev_db, owner, amount):
    db = SessionLocal()
    try:
        get_or_create_account(db, owner).cash_balance = amount
        db.commit()
    finally:
        db.close()


def assert_consistent(dev_db, owner, initial=INITIAL_BALANCE):
    """잔고 = 초기 - 매수금액 + 매도금액, 보유 수량 = 매수 수량 - 매도 수량, 보유 행은 종목당 최대 1개."""
    account, holdings, trades = snapshot(dev_db, owner)
    buy_amt = sum(float(t.price) * t.quantity for t in trades if t.side == "buy")
    sell_amt = sum(float(t.price) * t.quantity for t in trades if t.side == "sell")
    assert float(account.cash_balance) == pytest.approx(initial - buy_amt + sell_amt)
    net_qty = sum(t.quantity if t.side == "buy" else -t.quantity for t in trades)
    assert net_qty >= 0
    assert sum(h.quantity for h in holdings) == net_qty
    assert len(holdings) <= 1
    assert all(h.quantity > 0 for h in holdings)
    return account, holdings, trades


ROUNDS = 8  # 경쟁은 확률적이라 수정 전 코드의 재현율을 높이려고 여러 번 반복한다.


@pytest.mark.integration
class TestConcurrentOrders:
    def test_two_buys_beyond_balance_only_one_succeeds(self, dev_db, owner, company, monkeypatch):
        """시나리오 1: 잔고 100만 원, 80만 원짜리 매수 2건 동시 -> 정확히 1건 성공, 1건 잔고 부족."""
        for rnd in range(ROUNDS):
            reset_db = SessionLocal()
            try:
                reset_portfolio(reset_db, owner)
            finally:
                reset_db.close()
            set_balance(dev_db, owner, 1_000_000)
            fix_price(monkeypatch, 2)
            results = run_parallel([order_job(owner, company, "buy", 80)] * 2)  # 80주 * 10,000원 = 80만 원

            ok = [r for r, e in results if e is None]
            errs = [e for r, e in results if e is not None]
            assert len(ok) == 1, f"round {rnd}: 성공 {len(ok)}건, 예외 {[type(e).__name__ for e in errs]}"
            assert len(errs) == 1 and isinstance(errs[0], InsufficientBalance)
            account, holdings, trades = assert_consistent(dev_db, owner, initial=1_000_000)
            assert float(account.cash_balance) == 200_000
            assert len(trades) == 1 and holdings[0].quantity == 80

    def test_two_sells_beyond_holding_only_one_succeeds(self, dev_db, owner, company, monkeypatch):
        """시나리오 2: 보유 10주에서 7주 매도 2건 동시 -> 1건만 성공, 보유는 음수가 되지 않는다."""
        for rnd in range(ROUNDS):
            db = SessionLocal()
            try:
                reset_portfolio(db, owner)
            finally:
                db.close()
            fix_price(monkeypatch, 1)
            order_job(owner, company, "buy", 10)()
            fix_price(monkeypatch, 2)
            results = run_parallel([order_job(owner, company, "sell", 7)] * 2)

            ok = [r for r, e in results if e is None]
            errs = [e for r, e in results if e is not None]
            assert len(ok) == 1, f"round {rnd}: 성공 {len(ok)}건, 예외 {[type(e).__name__ for e in errs]}"
            assert len(errs) == 1 and isinstance(errs[0], InsufficientQuantity)
            account, holdings, trades = assert_consistent(dev_db, owner)
            assert [h.quantity for h in holdings] == [3]

    def test_concurrent_first_requests_create_one_account(self, dev_db, company, monkeypatch):
        """시나리오 3: 같은 소유자의 첫 요청이 동시에 와도 계좌 1개, 예외(500) 없음."""
        keys = []
        try:
            for _ in range(ROUNDS):
                key = f"captest_{secrets.token_hex(6)}"
                keys.append(key)
                barrier = threading.Barrier(6, timeout=20)

                def job(key=key, barrier=barrier):
                    db = SessionLocal()
                    try:
                        barrier.wait()
                        return float(get_or_create_account(db, key).cash_balance)
                    finally:
                        db.close()

                results = run_parallel([job] * 6)
                errs = [e for _, e in results if e is not None]
                assert not errs, f"예외 {[repr(e)[:80] for e in errs]}"
                assert all(r == INITIAL_BALANCE for r, _ in results)
                dev_db.rollback()
                assert dev_db.query(VirtualAccount).filter(VirtualAccount.device_id == key).count() == 1
        finally:
            dev_db.rollback()
            for key in keys:
                dev_db.query(VirtualAccount).filter(VirtualAccount.device_id == key).delete()
            dev_db.commit()

    def test_concurrent_first_buys_make_one_holding_row(self, dev_db, owner, company, monkeypatch):
        """보유 종목이 아직 없는 첫 매수들이 동시에 와도 보유 행은 1개(계좌 잠금 아래에서 처리)."""
        for rnd in range(ROUNDS):
            db = SessionLocal()
            try:
                reset_portfolio(db, owner)
            finally:
                db.close()
            fix_price(monkeypatch, 4)
            results = run_parallel([order_job(owner, company, "buy", 1)] * 4)
            errs = [e for _, e in results if e is not None]
            assert not errs, f"round {rnd}: {[repr(e)[:80] for e in errs]}"
            _, holdings, trades = assert_consistent(dev_db, owner)
            assert len(trades) == 4 and holdings[0].quantity == 4

    def test_order_and_reset_overlap_stays_consistent(self, dev_db, owner, company, monkeypatch):
        """시나리오 4: 주문과 초기화가 겹쳐도 최종 상태가 둘 중 한 순서와 같다(어긋난 중간 상태 없음)."""
        for rnd in range(ROUNDS * 2):
            db = SessionLocal()
            try:
                reset_portfolio(db, owner)
            finally:
                db.close()
            # 기존 보유 10주를 깔고, 매수 1건과 초기화 1건을 동시에 보낸다.
            fix_price(monkeypatch, 1)
            order_job(owner, company, "buy", 10)()
            barrier = fix_price(monkeypatch, 3)  # 주문 2건의 가격 조회 + 초기화 1건이 같이 출발

            def reset_job():
                s = SessionLocal()
                try:
                    barrier.wait()
                    return reset_portfolio(s, owner)
                finally:
                    s.close()

            results = run_parallel(
                [order_job(owner, company, "buy", 5), order_job(owner, company, "sell", 3), reset_job]
            )
            unexpected = [
                e for _, e in results if e is not None and not isinstance(e, (InsufficientQuantity, InsufficientBalance))
            ]
            assert not unexpected, f"round {rnd}: {[repr(e)[:80] for e in unexpected]}"
            assert_consistent(dev_db, owner)

    def test_mixed_parallel_orders_keep_ledger_balanced(self, dev_db, owner, company, monkeypatch):
        """시나리오 5: 매수·매도를 병렬로 섞어도 '초기 잔액 - 매수 합 + 매도 합 = 잔고', '체결 수량 합 = 보유 수량'."""
        fix_price(monkeypatch, 1)
        order_job(owner, company, "buy", 20)()  # 매도도 일부 성공하도록 시작 보유를 깔아둔다
        plan = [("buy", 3), ("sell", 5), ("buy", 7), ("sell", 4), ("buy", 2), ("sell", 6), ("buy", 9), ("sell", 8)] * 2
        fix_price(monkeypatch, len(plan))
        results = run_parallel([order_job(owner, company, side, q) for side, q in plan])
        allowed = (InsufficientQuantity, InsufficientBalance)
        unexpected = [e for _, e in results if e is not None and not isinstance(e, allowed)]
        assert not unexpected, [repr(e)[:80] for e in unexpected]
        _, _, trades = assert_consistent(dev_db, owner)
        assert len(trades) == 1 + sum(1 for _, e in results if e is None)
