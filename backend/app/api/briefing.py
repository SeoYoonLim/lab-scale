from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.api.deps import get_current_user
from app.auth import CurrentUser
from app.briefing_db import get_briefing
from app.i18n import MAX_LOCALE_LEN, is_default, resolve_locale
from app.db.session import SessionLocal

router = APIRouter(prefix="/api", tags=["briefing"])

DEFAULT_NEWS_LIMIT = 3
MAX_NEWS_LIMIT = 5


class Flag(BaseModel):
    type: Literal["concentration", "day_move", "week_move", "volume_spike", "new_disclosure"]
    scope: Literal["holding", "watchlist"]
    ticker: str
    name: str
    text: str  # 관찰 사실 한 줄(투자 권유 아님)
    value: float  # 근거 수치(unit 단위)
    threshold: float  # 이 flag가 켜지는 기준값(같은 단위)
    unit: Literal["%", "배", "건"]
    date: str | None  # 근거가 된 시세 날짜(YYYY-MM-DD)
    direction: Literal["up", "down"] | None  # day_move/week_move만 값이 있다


class NewsItem(BaseModel):
    title: str
    source: str | None
    published_at: str  # ISO 8601(KST)
    url: str | None


class DisclosureItem(BaseModel):
    title: str
    disclosure_type: str | None
    disclosed_at: str  # ISO 8601(KST)
    source_url: str | None


class Quote(BaseModel):
    code: str
    name: str
    date: str
    close: float
    change_pct: float | None  # 직전 값이 없으면 null


class Market(BaseModel):
    kospi: Quote | None
    kosdaq: Quote | None
    usd_krw: Quote | None


class WeightItem(BaseModel):
    ticker: str
    name: str
    weight_pct: float


class Portfolio(BaseModel):
    has_account: bool
    initial_balance: float
    cash_balance: float | None
    holdings_value: float | None
    total_asset: float | None
    profit_loss: float | None  # 총자산 - 초기 자금
    profit_loss_pct: float | None
    day_profit_loss: float | None  # 보유수량 x (최신 종가 - 직전 종가)의 합
    top_weight: WeightItem | None
    weights: list[WeightItem]  # 비중 큰 순
    message: str | None


class BriefingItem(BaseModel):
    ticker: str
    name: str
    asset_type: Literal["stock", "etf"]
    data_available: bool
    latest_close: float | None
    latest_date: str | None
    day_change_pct: float | None
    week_change_pct: float | None
    volume: int | None
    avg_volume_20d: float | None
    volume_ratio: float | None
    news: list[NewsItem]
    disclosures: list[DisclosureItem]
    disclosure_count: int
    notice: str | None
    flags: list[Flag]


class HoldingBriefingItem(BriefingItem):
    quantity: int
    avg_price: float
    eval_amount: float | None
    weight_pct: float | None
    profit_loss: float | None
    profit_loss_pct: float | None
    day_profit_loss: float | None


class BriefingResponse(BaseModel):
    generated_at: str
    data_date: str | None
    stale: bool
    stale_notice: str | None
    message: str | None
    market: Market
    portfolio: Portfolio
    holdings: list[HoldingBriefingItem]
    watchlist: list[BriefingItem]
    highlights: list[Flag]
    disclaimer: str


@router.get("/briefing", response_model=BriefingResponse)
def read_briefing(
    news_limit: int = Query(DEFAULT_NEWS_LIMIT, ge=1, le=MAX_NEWS_LIMIT, description="종목당 뉴스 수(최근 7일)"),
    locale: str | None = Query(None, max_length=MAX_LOCALE_LEN, description="문장 언어(ko-KR 기본, en-US, ja-JP, zh-CN). 없으면 계정 설정. 숫자·뉴스 제목은 같다"),
    user: CurrentUser = Depends(get_current_user),
) -> dict:
    """로그인 사용자의 보유종목·관심종목 기준 오늘의 브리핑. LLM 없이 코드가 계산한 값만 담는다(읽기 전용, 계좌를 만들지 않음)."""
    locale = resolve_locale(locale, user.preferred_locale)
    db = SessionLocal()
    try:
        brief = get_briefing(db, user.owner_key, news_limit)
    finally:
        db.close()
    if is_default(locale):
        return brief
    from app import briefing_i18n

    return briefing_i18n.localize(brief, locale)
