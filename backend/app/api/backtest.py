"""POST /api/backtest: 과거 종가로 전략(buy_and_hold / sma_cross / dca)을 시험한다. LLM 없음, 결과는 저장하지 않는다.

순서: 인증(401) → 본문 검증(422) → 레이트 리밋(429) → 종목 해석(엄격 모드, 못 찾으면 후보 안내 404) → 계산.
데이터가 모자라 계산할 수 없으면(공통 거래일 30일 미만) 400 + 이유다. 진단의 "보유 종목 없음", 주문의 "잔고 부족"처럼
형식은 맞지만 처리할 수 없는 요청에 이 프로젝트가 쓰는 코드다(422는 요청 값 자체가 규칙에 어긋날 때).
"""

from datetime import date, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator, model_validator

from app import rate_limit
from app.api.deps import get_current_user
from app.auth import CurrentUser
from app.backtest import (
    DEFAULT_FEE_RATE,
    DEFAULT_SELL_TAX_RATE,
    MAX_RATE,
    MAX_TICKERS,
    BacktestError,
    ParamError,
)
from app.backtest_db import backtest
from app.briefing import KST
from app.db.session import SessionLocal
from app.tools.company_resolver import resolve_company

router = APIRouter(prefix="/api", tags=["backtest"])

MAX_YEARS = 5
MIN_START = date(1990, 1, 1)
MIN_INITIAL_CASH = 100_000
MAX_INITIAL_CASH = 10_000_000_000
DEFAULT_INITIAL_CASH = 10_000_000


class BacktestRequest(BaseModel):
    tickers: list[str] = Field(min_length=1, max_length=MAX_TICKERS)
    strategy: Literal["buy_and_hold", "sma_cross", "dca"]
    params: dict[str, Any] = Field(default_factory=dict)
    start: date
    end: date
    initial_cash: float = Field(DEFAULT_INITIAL_CASH, ge=MIN_INITIAL_CASH, le=MAX_INITIAL_CASH, allow_inf_nan=False)
    fee_rate: float = Field(DEFAULT_FEE_RATE, ge=0, le=MAX_RATE, allow_inf_nan=False)
    sell_tax_rate: float = Field(DEFAULT_SELL_TAX_RATE, ge=0, le=MAX_RATE, allow_inf_nan=False)

    @field_validator("tickers")
    @classmethod
    def _tickers(cls, v: list[str]) -> list[str]:
        cleaned = [t.strip() for t in v]
        if any(not t or len(t) > 50 for t in cleaned):
            raise ValueError("종목은 1~50자여야 합니다.")
        if len({t.upper() for t in cleaned}) != len(cleaned):
            raise ValueError("같은 종목을 두 번 넣을 수 없습니다.")
        return cleaned

    @model_validator(mode="after")
    def _period(self):
        today = datetime.now(KST).date()
        if self.start < MIN_START:
            raise ValueError(f"start는 {MIN_START.isoformat()} 이후여야 합니다.")
        if self.end > today:
            raise ValueError("end는 오늘 이후일 수 없습니다.")
        if self.start >= self.end:
            raise ValueError("start는 end보다 빨라야 합니다.")
        try:
            limit = self.end.replace(year=self.end.year - MAX_YEARS)
        except ValueError:
            limit = self.end.replace(year=self.end.year - MAX_YEARS, day=28)
        if self.start < limit:
            raise ValueError(f"기간은 최대 {MAX_YEARS}년입니다.")
        return self


class TickerOut(BaseModel):
    ticker: str
    name: str


class InputOut(BaseModel):
    tickers: list[TickerOut]
    strategy: str
    params: dict[str, int | float]
    start: str
    end: str
    initial_cash: float
    fee_rate: float
    sell_tax_rate: float


class PeriodOut(BaseModel):
    requested_start: str
    requested_end: str
    start: str
    end: str
    trading_days: int
    adjusted: bool


class Drawdown(BaseModel):
    pct: float
    peak_date: str | None
    trough_date: str | None
    recovery_date: str | None
    duration_days: int


class Metrics(BaseModel):
    final_value: float
    total_return_pct: float | None
    cagr_pct: float | None
    max_drawdown: Drawdown
    annualized_volatility_pct: float | None
    sharpe_like: float | None
    trade_count: int
    buy_count: int
    sell_count: int
    fees_paid: float
    taxes_paid: float
    ending_cash: float


class PerTicker(BaseModel):
    ticker: str
    name: str
    start_value: float
    final_value: float
    return_pct: float | None
    quantity: int
    trade_count: int


class BenchmarkOut(BaseModel):
    index_code: str
    index_name: str
    start_date: str
    end_date: str
    return_pct: float | None
    excess_return_pct: float | None
    covers_full_period: bool


class Point(BaseModel):
    date: str
    total_value: float
    cash: float


class Curve(BaseModel):
    points: list[Point]
    total_points: int
    sampled: bool
    max_points: int


class TradeOut(BaseModel):
    ticker: str
    side: Literal["buy", "sell"]
    signal_date: str
    date: str
    quantity: int
    price: float
    amount: float
    fee: float
    tax: float
    cash_after: float


class Trades(BaseModel):
    items: list[TradeOut]
    total: int
    truncated: bool
    max_items: int


class Warning_(BaseModel):
    ticker: str
    name: str
    kind: Literal["jump", "non_positive", "flat_run"]
    date: str
    detail: str
    value: float | None


class BacktestResponse(BaseModel):
    generated_at: str
    input: InputOut
    period: PeriodOut
    metrics: Metrics
    per_ticker: list[PerTicker]
    benchmark: BenchmarkOut | None
    curve: Curve
    trades: Trades
    assumptions: list[str]
    data_warnings: list[Warning_]
    notices: list[str]
    disclaimer: str


def _param_error(e: ParamError) -> HTTPException:
    loc = ["body", e.field] if e.field in ("tickers", "fee_rate", "start", "strategy") else ["body", "params", e.field]
    return HTTPException(status_code=422, detail=[{"type": "value_error", "loc": loc, "msg": str(e)}])


@router.post("/backtest", response_model=BacktestResponse, response_model_exclude_none=False)
def create_backtest(request: BacktestRequest, user: CurrentUser = Depends(get_current_user)) -> dict:
    """요청마다 계산한다(저장하지 않음). 모든 숫자는 코드가 DB 종가로 계산한다."""
    rate_limit.check(rate_limit.backtest, user.owner_key, "RATE_LIMIT_BACKTEST_PER_MIN")
    db = SessionLocal()
    try:
        companies = []
        for raw in request.tickers:
            res = resolve_company(db, raw, allow_fuzzy=False)
            if res.company is None:
                raise HTTPException(status_code=404, detail=res.message)
            if any(c.id == res.company.id for c in companies):
                raise HTTPException(status_code=422, detail=[{"type": "value_error", "loc": ["body", "tickers"],
                                                              "msg": "같은 종목을 두 번 넣을 수 없습니다."}])
            companies.append(res.company)
        try:
            return backtest(db, companies, request.strategy, request.params, request.start, request.end,
                            request.initial_cash, request.fee_rate, request.sell_tax_rate)
        except ParamError as e:
            raise _param_error(e) from None
        except BacktestError as e:
            raise HTTPException(status_code=400, detail=str(e)) from None
    finally:
        db.close()
