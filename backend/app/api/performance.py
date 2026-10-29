"""GET /api/portfolio/performance: 모의투자 자산 곡선과 성과 지표(LLM 없음, 읽기 전용).

app/api/portfolio.py(주문·평가)와 분리한 이유: 그 흐름을 건드리지 않고 같은 prefix에 읽기 전용 라우트만 더한다.
계산은 app/performance.py, DB 조회는 app/performance_db.py.
"""

from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.api.deps import get_owner_key
from app.db.session import SessionLocal
from app.performance_db import get_performance

router = APIRouter(prefix="/api/portfolio", tags=["portfolio"])


class Summary(BaseModel):
    start_value: float
    end_value: float
    cash_balance: float
    eval_amount: float
    profit_loss: float
    cumulative_return_pct: float | None
    period_return_pct: float | None
    cash_weight_pct: float | None


class MaxDrawdown(BaseModel):
    pct: float
    peak_date: str | None
    trough_date: str | None
    recovery_date: str | None
    duration_days: int


class Risk(BaseModel):
    max_drawdown: MaxDrawdown
    daily_volatility_pct: float | None
    annualized_volatility_pct: float | None
    daily_return_count: int


class TradeStats(BaseModel):
    count: int
    buys: int
    sells: int
    wins: int
    losses: int
    even: int
    win_rate_pct: float | None
    realized_profit_loss: float
    pending_count: int


class Contribution(BaseModel):
    ticker: str
    company_name: str
    profit_loss: float
    contribution_pct: float | None
    quantity: int
    eval_amount: float
    unrealized_profit_loss: float | None
    realized_profit_loss: float


class Benchmark(BaseModel):
    index_code: str
    index_name: str
    start_date: str
    end_date: str
    index_return_pct: float | None
    portfolio_return_pct: float | None
    excess_return_pct: float | None
    covers_full_period: bool


class CurvePoint(BaseModel):
    date: str
    total_asset: float
    cash_balance: float
    eval_amount: float
    return_pct: float | None


class Curve(BaseModel):
    points: list[CurvePoint]
    total_points: int
    sampled: bool
    max_points: int


class DataGap(BaseModel):
    ticker: str
    company_name: str
    kind: Literal["previous_close", "trade_price"]
    days: int
    first_date: str
    last_date: str


class PerformanceResponse(BaseModel):
    generated_at: str
    period: Literal["1m", "3m", "6m", "all"]
    data_date: str | None
    stale: bool
    stale_notice: str | None
    start_date: str | None
    end_date: str | None
    initial_balance: float
    message: str | None
    summary: Summary
    risk: Risk
    trades: TradeStats
    contributions: list[Contribution]
    benchmark: Benchmark | None
    curve: Curve
    data_gaps: list[DataGap]
    notices: list[str]
    disclaimer: str


@router.get("/performance", response_model=PerformanceResponse, response_model_exclude_none=False)
def read_performance(
    period: Literal["1m", "3m", "6m", "all"] = Query("all", description="기간: 1m | 3m | 6m | all"),
    owner_key: str = Depends(get_owner_key),
) -> dict:
    """로그인 사용자의 모의투자 자산 곡선(거래일 종가 기준)과 성과 지표. 계좌를 만들지 않는다."""
    db = SessionLocal()
    try:
        return get_performance(db, owner_key, period)
    finally:
        db.close()
