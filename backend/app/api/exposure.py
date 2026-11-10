"""GET /api/portfolio/exposure: 모의투자 보유 종목과 보유 ETF 구성종목을 합친 기업별 실질 노출(LLM 없음, DB만 읽는 읽기 전용).

app/api/portfolio.py(주문·평가)와 분리한 이유: 그 흐름을 건드리지 않고 같은 prefix에 읽기 전용 라우트만 더한다.
계산은 app/exposure.py, DB 조회는 app/exposure_db.py. 성과 추적·브리핑 같은 기존 조회 API처럼 레이트 리밋은 없다(DB 읽기 쿼리 3개).
"""

from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.api.deps import get_current_user
from app.auth import CurrentUser
from app.db.session import SessionLocal
from app.exposure import DEFAULT_LIMIT, MAX_LIMIT
from app.exposure_db import get_exposure, get_look_through
from app.i18n import MAX_LOCALE_LEN, is_default, resolve_locale

router = APIRouter(prefix="/api/portfolio", tags=["portfolio"])


class Summary(BaseModel):
    total_eval_amount: float
    holding_count: int
    stock_amount: float
    etf_amount: float
    decomposed_etf_amount: float
    undecomposed_etf_amount: float
    companies_amount: float
    other_amount: float
    other_weight_pct: float
    company_count: int


class ViaEtf(BaseModel):
    ticker: str
    name: str
    amount: float
    etf_weight_pct: float


class CompanyExposure(BaseModel):
    rank: int
    name: str
    code: str | None
    linked: bool
    direct_amount: float
    via_etf_amount: float
    via_etfs: list[ViaEtf]
    total_amount: float
    effective_weight_pct: float
    direct_weight_pct: float


class DecomposedEtf(BaseModel):
    ticker: str
    name: str
    eval_amount: float
    weight_pct: float
    holdings_as_of: str | None
    stale: bool
    stale_notice: str | None
    constituent_count: int
    weight_sum_pct: float


class UndecomposedEtf(BaseModel):
    ticker: str
    name: str
    eval_amount: float
    weight_pct: float
    reason_code: str
    reason: str


class Flag(BaseModel):
    kind: str
    name: str
    code: str | None
    value_pct: float
    threshold_pct: float
    message: str


class DataWarning(BaseModel):
    kind: str
    message: str


class ExposureResponse(BaseModel):
    generated_at: str
    valuation_date: str | None
    stale: bool
    stale_notice: str | None
    etf_holdings_stale: bool
    message: str | None
    limit: int
    summary: Summary
    companies: list[CompanyExposure]
    overlaps: list[CompanyExposure]
    decomposed_etfs: list[DecomposedEtf]
    undecomposed_etfs: list[UndecomposedEtf]
    flags: list[Flag]
    data_warnings: list[DataWarning]
    disclaimer: str


class LookThroughSummary(BaseModel):
    cash_balance: float
    holdings_value: float
    total_asset: float
    stock_amount: float
    etf_amount: float
    direct_amount: float
    via_etf_amount: float
    companies_amount: float
    linked_via_amount: float
    unlinked_via_amount: float
    nested_etf_amount: float
    residual_amount: float
    undecomposed_etf_amount: float
    unidentified_amount: float
    unidentified_weight_pct: float | None
    over_allocated_amount: float
    cash_weight_pct: float | None
    companies_weight_pct: float | None
    holding_count: int
    priced_holding_count: int
    unpriced_count: int
    company_count: int
    direct_top1_weight_pct: float | None
    look_through_top1: dict | None


class LookThroughResponse(BaseModel):
    """view=look_through(2026-11-06, app/look_through). 비중의 분모는 총자산(현금 + 주식 + ETF). 필드 설명은 API.md."""

    generated_at: str
    calc_version: str
    locale: str
    status: Literal["ok", "empty", "no_prices"]
    message: str | None
    has_account: bool
    valuation_date: str | None
    expected_date: str
    price_lag_business_days: int | None
    stale: bool
    stale_notice: str | None
    etf_holdings_stale: bool
    limit: int
    summary: LookThroughSummary
    companies: list[dict[str, Any]]
    overlaps: list[dict[str, Any]]
    etfs: list[dict[str, Any]]
    unpriced: list[dict[str, Any]]
    sector: dict[str, Any]
    flags: list[dict[str, Any]]
    data_warnings: list[dict[str, Any]]
    disclaimer: str


@router.get("/exposure", response_model=ExposureResponse | LookThroughResponse, response_model_exclude_none=False)
def read_exposure(
    limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT, description="실질 비중이 큰 기업을 몇 개까지 보여줄지"),
    locale: str | None = Query(None, max_length=MAX_LOCALE_LEN, description="문장 언어(ko-KR 기본, en-US, ja-JP, zh-CN). 없으면 계정 설정. 숫자는 같다"),
    view: Literal["default", "look_through"] = Query(
        "default", description="look_through면 총자산 대비 직접·간접 노출, ETF별 기여도·데이터 품질, 중첩 ETF·미확인 노출(2026-11-06). 기본은 기존 응답 그대로"),
    user: CurrentUser = Depends(get_current_user),
) -> dict:
    """로그인 사용자의 보유 종목 + 보유 ETF 구성종목을 합친 기업별 실질 노출. 보유가 없으면 200 + 빈 목록 + message. 계좌를 만들지 않는다."""
    locale = resolve_locale(locale, user.preferred_locale)
    db = SessionLocal()
    try:
        if view == "look_through":
            from app import look_through_i18n

            return look_through_i18n.localize(get_look_through(db, user.owner_key, limit), locale)
        result = get_exposure(db, user.owner_key, limit)
    finally:
        db.close()
    if is_default(locale):
        return result
    from app import exposure_i18n

    return exposure_i18n.localize(result, locale)
