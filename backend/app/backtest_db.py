"""백테스트 입력을 DB에서 읽는 얇은 계층(읽기 전용). 계산은 app/backtest.py가 한다.

쿼리: 종가 1(company_id IN (...), 기간 안) + 코스피 지수 1. 장 마감(평일 15:30 KST) 전에는 오늘 날짜 행을 뺀다
(app.briefing.drop_open_session, 브리핑·성과 추적과 같은 규칙).
"""

from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.backtest import BENCHMARK_CODE, PAST_PERFORMANCE, run_backtest
from app.briefing import KST, drop_open_session
from app.disclaimer import DISCLAIMER
from app.models import Company, MarketIndex, StockPrice


def load_prices(db: Session, companies: list[Company], start: date, end: date, now: datetime) -> dict:
    ids = {c.id: c.ticker for c in companies}
    rows = db.execute(
        select(StockPrice.company_id, StockPrice.price_date, StockPrice.close_price)
        .where(StockPrice.company_id.in_(list(ids)), StockPrice.price_date >= start, StockPrice.price_date <= end)
        .order_by(StockPrice.company_id, StockPrice.price_date)
    ).all()
    prices: dict[str, list] = {c.ticker: [] for c in companies}
    for r in drop_open_session(rows, now, day=lambda r: r.price_date):
        prices[ids[r.company_id]].append((r.price_date, float(r.close_price)))
    index_rows = db.execute(
        select(MarketIndex.price_date, MarketIndex.close_price)
        .where(MarketIndex.index_code == BENCHMARK_CODE, MarketIndex.price_date >= start, MarketIndex.price_date <= end)
        .order_by(MarketIndex.price_date)
    ).all()
    return {"prices": prices, "index": [(d, float(c)) for d, c in drop_open_session(index_rows, now)]}


def backtest(db: Session, companies: list[Company], strategy: str, params: dict, start: date, end: date,
             initial_cash: float, fee_rate: float, sell_tax_rate: float, now: datetime | None = None) -> dict:
    now = (now or datetime.now(KST)).astimezone(KST)
    data = load_prices(db, companies, start, end, now)
    out = run_backtest(data["prices"], {c.ticker: c.name for c in companies}, strategy, params, start, end,
                       initial_cash, fee_rate, sell_tax_rate, index_series=data["index"])
    return {"generated_at": now.isoformat(), **out, "disclaimer": f"{DISCLAIMER} {PAST_PERFORMANCE}"}
