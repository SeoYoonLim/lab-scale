"""GET /api/portfolio/performance: 모의투자 자산 곡선과 성과 지표(LLM 없음, 읽기 전용).

app/api/portfolio.py(주문·평가)와 분리한 이유: 그 흐름을 건드리지 않고 같은 prefix에 읽기 전용 라우트만 더한다.
계산은 app/performance.py, DB 조회는 app/performance_db.py.
"""

from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.api.deps import get_current_user
from app.auth import CurrentUser
from app.i18n import MAX_LOCALE_LEN, resolve_locale
from app.db.session import SessionLocal
from app.performance_db import get_performance, get_performance_view

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
    period: Literal["1m", "3m", "6m", "1y", "all"]
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


class PerformanceViewResponse(BaseModel):
    """view=benchmark(2026-11-06, app.performance_compare). 필드 설명은 API.md "성과·벤치마크 비교" 절."""

    generated_at: str
    calc_version: str
    locale: str
    status: Literal["ok", "partial", "no_account", "no_trades", "pending_only", "no_prices", "history_unavailable"]
    message: str | None
    history_verified: bool
    period: Literal["1m", "3m", "6m", "1y", "all"]
    requested_start_date: str | None
    start_date: str | None
    end_date: str | None
    data_date: str | None
    expected_date: str
    data_lag_business_days: int | None
    initial_balance: float
    benchmark_code: Literal["KS11", "KQ11"]
    benchmark_name: str
    summary: dict[str, Any]
    metrics: dict[str, Any]
    series: dict[str, Any]
    comparison: dict[str, Any]
    estimated_dates: list[str]
    cash_flows: dict[str, Any]
    price_basis: dict[str, Any]
    warnings: list[dict[str, Any]]
    disclaimer: str


@router.get("/performance", response_model=PerformanceResponse | PerformanceViewResponse, response_model_exclude_none=False)
def read_performance(
    period: Literal["1m", "3m", "6m", "1y", "all"] = Query("all", description="기간: 1m | 3m | 6m | 1y | all (1y는 2026-11-06 추가)"),
    view: Literal["default", "benchmark"] = Query(
        "default", description="benchmark면 상태·이력 검증·공통 거래일 지수 비교·정규화 시계열(2026-11-06). 기본은 기존 응답 그대로"),
    benchmark: Literal["KS11", "KQ11", "KOSPI", "KOSDAQ"] = Query("KS11", description="view=benchmark의 비교 지수(DB에 있는 코스피·코스닥만)"),
    locale: str | None = Query(None, max_length=MAX_LOCALE_LEN, description="view=benchmark 문장 언어. 숫자는 같다"),
    user: CurrentUser = Depends(get_current_user),
) -> dict:
    """로그인 사용자의 모의투자 자산 곡선(거래일 종가 기준)과 성과 지표. 계좌를 만들지 않는다."""
    db = SessionLocal()
    try:
        if view == "benchmark":
            from app import performance_i18n
            from app.performance_compare import BENCHMARK_ALIASES

            result = get_performance_view(db, user.owner_key, period, BENCHMARK_ALIASES[benchmark])
            return performance_i18n.localize(result, resolve_locale(locale, user.preferred_locale))
        return get_performance(db, user.owner_key, period)
    finally:
        db.close()
