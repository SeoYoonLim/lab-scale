"""성과 추적 입력을 DB에서 읽는 얇은 계층(읽기 전용: 계좌를 만들지 않고 commit도 하지 않는다). 계산은 app/performance.py가 한다.

쿼리 수는 보유·거래 종목 수와 상관없이 고정이다: 계좌 1 + 체결 1 + 거래일 달력 1 + 종가 1(company_id IN (...)) + 지수 1.
체결이 없으면 종가 쿼리를 건너뛴다(4). 장 마감 전(평일 15:30 KST) 오늘 날짜의 시세·지수 행은 briefing과 같은 규칙
(app.briefing.drop_open_session)으로 뺀다.
"""

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.briefing import KST, drop_open_session
from app.data_status import is_stale
from app.disclaimer import DISCLAIMER
from app.models import Company, MarketIndex, StockPrice, Trade, VirtualAccount
from app.models.virtual_account import INITIAL_BALANCE
from app.performance import BENCHMARK_CODE, TradeIn, compute_performance

CASH_MISMATCH = "체결 내역을 다시 계산한 현금({replayed:,.0f}원)이 현재 현금 잔고({actual:,.0f}원)와 다릅니다. 내역 일부가 빠졌을 수 있습니다."


def load_inputs(db: Session, owner_key: str, now: datetime) -> dict:
    account = db.get(VirtualAccount, owner_key)
    rows = db.execute(
        select(Trade.executed_at, Trade.side, Trade.quantity, Trade.price, Trade.company_id, Company.ticker, Company.name)
        .join(Company, Company.id == Trade.company_id)
        .where(Trade.device_id == owner_key)
        .order_by(Trade.executed_at, Trade.id)
    ).all()
    trades = [
        TradeIn(r.executed_at.astimezone(KST).date(), r.ticker, r.name, r.side, int(r.quantity), float(r.price)) for r in rows
    ]

    calendar = [d for (d,) in db.execute(select(StockPrice.price_date).distinct().order_by(StockPrice.price_date))]
    calendar = drop_open_session(calendar, now, day=lambda d: d)

    closes: dict[str, list] = {}
    ids = {r.company_id: r.ticker for r in rows}
    if ids:
        price_rows = db.execute(
            select(StockPrice.company_id, StockPrice.price_date, StockPrice.close_price)
            .where(StockPrice.company_id.in_(list(ids)))
            .order_by(StockPrice.company_id, StockPrice.price_date)
        ).all()
        for r in drop_open_session(price_rows, now, day=lambda r: r.price_date):
            closes.setdefault(ids[r.company_id], []).append((r.price_date, float(r.close_price)))

    index_rows = db.execute(
        select(MarketIndex.price_date, MarketIndex.close_price)
        .where(MarketIndex.index_code == BENCHMARK_CODE)
        .order_by(MarketIndex.price_date)
    ).all()
    index_series = [(d, float(c)) for d, c in drop_open_session(index_rows, now)]

    return {
        "cash_balance": float(account.cash_balance) if account is not None else None,
        "trades": trades,
        "closes": closes,
        "calendar": calendar,
        "index_series": index_series,
    }


def replayed_cash(trades: list[TradeIn], initial: float) -> float:
    """모든 체결(종가 반영 전 것 포함)을 주문과 같은 반올림으로 재생한 현금."""
    cash = float(initial)
    for t in trades:
        amount = round(t.price * t.quantity, 2)
        cash = round(cash - amount if t.side == "buy" else cash + amount, 2)
    return cash


def get_performance(db: Session, owner_key: str, period: str, now: datetime | None = None) -> dict:
    now = (now or datetime.now(KST)).astimezone(KST)
    inputs = load_inputs(db, owner_key, now)
    out = compute_performance(inputs["trades"], inputs["closes"], inputs["calendar"], inputs["index_series"],
                              period, INITIAL_BALANCE)
    actual = inputs["cash_balance"]
    if actual is not None and inputs["trades"]:
        replayed = replayed_cash(inputs["trades"], INITIAL_BALANCE)
        if abs(replayed - actual) > 0.01:
            out["notices"].append(CASH_MISMATCH.format(replayed=replayed, actual=actual))

    data_date = inputs["calendar"][-1] if inputs["calendar"] else None
    stale = is_stale(data_date, now.date())
    return {
        "generated_at": now.isoformat(),
        "data_date": data_date.isoformat() if data_date else None,
        "stale": stale,
        "stale_notice": (f"마지막 시세는 {data_date.isoformat()} 기준입니다." if data_date else "수집된 시세가 없습니다.")
        if stale else None,
        **out,
        "disclaimer": DISCLAIMER,
    }
