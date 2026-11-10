from datetime import date, datetime, timezone
from typing import Literal

from fastapi import APIRouter, HTTPException, Path, Query
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel

from app import stock_feed
from app.api.market import LastAttempt
from app.db.session import SessionLocal
from app.event_reaction_service import build_event_reactions
from app.pagination import MAX_OFFSET
from app.price_history import build_price_history, history_rows
from app.realtime_price import get_realtime_price_data
from app.tools.company_resolver import resolve_company

router = APIRouter(prefix="/api/stocks", tags=["stocks"])


class RealtimePriceResponse(BaseModel):
    ticker: str
    company_name: str
    current_price: float
    # 상위 소스(네이버) 조회가 실패하거나 장중이 아니면 전일대비 금액은 모른다(폴백은 종가만 있음).
    change_amount: float | None = None
    change_pct: float | None = None
    # 가격의 기준 시각. 실시간이면 네이버가 체결 시각을, 폴백이면 저장된 종가의 날짜를 담는다.
    as_of: str
    # 이 API 응답을 만든 시각(서버 now, UTC). 캐싱된 값을 돌려줬어도 호출마다 새로 채운다.
    queried_at: str
    is_realtime: bool
    source: Literal["naver", "fallback"]
    # 입력이 별칭/유사 종목명이라 보정됐을 때만 채워진다(company_resolver 공통 동작).
    corrected_from: str | None = None


@router.get("/{ticker}/realtime-price", response_model=RealtimePriceResponse)
def get_stock_realtime_price(
    ticker: str = Path(min_length=1, max_length=50),
) -> RealtimePriceResponse:
    """종목 현재가(비공식 소스 기반 실시간 시세, 서버에서 종목별 3~5초 캐싱).

    상위 소스(네이버) 호출이 실패하거나 장중이 아니면 예외 대신 DB에 저장된 최근 종가로 폴백하고,
    `is_realtime=false`, `source="fallback"`로 표시한다. 비공식 소스라 언제든 막히거나 형식이
    바뀔 수 있다는 전제의 설계다(자세한 내용은 `app/realtime_price.py` 모듈 docstring 참고).
    """
    db = SessionLocal()
    try:
        res = resolve_company(db, ticker)
        if res.company is None:
            raise HTTPException(status_code=404, detail=res.message)
        company = res.company

        data = get_realtime_price_data(db, company)
        if data is None:
            raise HTTPException(
                status_code=503,
                detail=f"'{company.name}'의 시세를 가져올 수 없습니다. 실시간 소스와 저장된 주가 데이터가 모두 없습니다.",
            )

        extra = {"corrected_from": res.corrected_from} if res.corrected_from else {}
        return RealtimePriceResponse(
            ticker=company.ticker,
            company_name=company.name,
            queried_at=datetime.now(timezone.utc).isoformat(),
            **data,
            **extra,
        )
    finally:
        db.close()


class Horizon(BaseModel):
    pct: float | None
    index_pct: float | None
    excess_pct: float | None
    available: bool
    reason: str | None


class Returns(BaseModel):
    d1: Horizon
    d5: Horizon
    d20: Horizon


class EventReaction(BaseModel):
    event_type: Literal["disclosure", "news"]
    title: str
    url: str | None
    event_at: str | None
    event_date: str | None
    time_known: bool
    base_date: date | None
    base_close: float | None
    reaction_start_date: date | None
    returns: Returns
    volume_ratio: float | None
    volume_sample_days: int
    other_events_in_window: int
    note: str | None


class EventReactionsResponse(BaseModel):
    ticker: str
    company_name: str
    asset_type: str
    price_as_of: date | None
    market_index: str | None
    events: list[EventReaction]
    summary: dict[str, int | float | None]
    disclaimer: str


@router.get("/{ticker}/event-reactions", response_model=EventReactionsResponse)
def get_event_reactions(
    ticker: str = Path(min_length=1, max_length=50),
    event_type: Literal["disclosure", "news", "all"] = "all",
    limit: int = Query(10, ge=1, le=30, description="유형별 최대 사건 수(all이면 공시·뉴스 각각 최신 limit건)"),
    since_days: int = Query(60, ge=1, le=120),
) -> dict:
    """뉴스·공시가 나온 시점 이후 주가 반응(1/5/20거래일 수익률, 지수 대비 초과수익률, 거래량 비율). 공개.

    계산 정의와 한계는 `app/event_reaction.py` 모듈 docstring과 API.md 참고. 시간적 선후관계일 뿐 인과관계가 아니다.
    """
    db = SessionLocal()
    try:
        res = resolve_company(db, ticker)
        if res.company is None:
            raise HTTPException(status_code=404, detail=res.message)
        return build_event_reactions(db, res.company, event_type, limit, since_days)
    finally:
        db.close()


class PriceHistoryItem(BaseModel):
    date: date
    close: float | None
    volume: int | None
    change_pct: float | None
    open: float | None
    high: float | None
    low: float | None


class PriceHistoryResponse(BaseModel):
    ticker: str
    company_name: str
    asset_type: Literal["stock", "etf"]
    days: int
    count: int
    price_as_of: date | None
    # 2026-11-10(41차) 추가. available_count: 이 종목의 저장된 주가 행 전체 수(요청한 days와 무관). 180·365 같은 긴 기간 버튼을
    # 켤지 정하는 데 쓴다. first_date: 가장 이른 저장 날짜. missing_trading_days: items 첫날~끝날 사이 KRX 거래일인데 행이 없는 날 수.
    available_count: int
    first_date: date | None
    missing_trading_days: int
    items: list[PriceHistoryItem]


MAX_PRICE_HISTORY_DAYS = 365


@router.get("/{ticker}/price-history", response_model=PriceHistoryResponse)
def get_price_history(
    ticker: str = Path(min_length=1, max_length=50),
    days: int = Query(90, ge=1, le=MAX_PRICE_HISTORY_DAYS,
                      description="최근 N거래일(달력 일 아님, 그 종목의 저장된 주가 행 수 기준). 1~365, 기본 90"),
) -> dict:
    """주가 추이 차트용 일별 시세(종가·거래량·등락률·시가·고가·저가). 날짜 오름차순. 공개.

    데이터가 days보다 적으면 있는 만큼만 주고 `count`로 알 수 있다(에러 아님). 주가가 아직 없으면 200에
    items=[], price_as_of=null. 저장된 일별 데이터라 장중 시세가 아니다(현재가는 realtime-price).
    2026-11-10(41차): days 상한 90 -> 365, available_count·first_date·missing_trading_days 추가(기존 필드·기본값은 그대로).
    """
    db = SessionLocal()
    try:
        res = resolve_company(db, ticker)
        if res.company is None:
            raise HTTPException(status_code=404, detail=res.message)
        rows, available, first = history_rows(db, res.company.id, days)
        return build_price_history(res.company, rows, days, available, first)
    finally:
        db.close()


# ---------- 종목별 뉴스·공시 목록(2026-11-10, 41차). 계산·한계는 app/stock_feed.py 모듈 docstring ----------


class FeedFilters(BaseModel):
    date_from: date | None
    date_to: date | None
    order: Literal["asc", "desc"]


class FeedFreshness(BaseModel):
    last_collected_at: str | None  # 이 데이터(뉴스 또는 공시) 전체의 마지막 저장 시각(ISO 8601, KST). 수집이 멈췄는지 보는 값
    collection_lag_weekdays: int | None  # 위 날짜가 최근 평일보다 늦은 평일 수(뉴스·공시는 KRX 휴장과 무관해 평일 기준)
    stale: bool  # collection_lag_weekdays >= 2이거나 수집 기록이 없으면 true
    company_latest_at: str | None  # 이 종목의 가장 최근 기사 게시 시각 / 공시 접수일(KST). 오래돼도 수집 지연이 아닐 수 있다
    # refresh_data 뉴스·공시 단계("news")의 마지막 실제 시도. status=failed면 수집 실패, success인데 items가 비면 저장된 항목이 원래 없음.
    last_collection: LastAttempt | None


class StockNewsItem(BaseModel):
    id: int
    title: str
    summary: str | None  # 수집 때 네이버 뉴스 검색 API가 준 기사 앞부분 발췌(우리가 만든 요약이 아니다)
    source: str | None  # 언론사 도메인(예: www.mk.co.kr)
    url: str | None
    published_at: str | None  # 기사 게시 시각(ISO 8601, KST)
    collected_at: str | None  # 이 행을 처음 저장한 시각(ISO 8601, KST)
    is_relevant: bool | None  # 수집 때 저장한 종목 관련성 판정(true 또는 판정 없음 null). false인 기사는 목록에서 뺀다
    related_tickers: list[str]  # 같은 URL이 연결된 다른 종목 코드(이 종목 제외, 오름차순)


class NewsListResponse(BaseModel):
    ticker: str
    company_name: str
    asset_type: Literal["stock", "etf"]
    total: int
    limit: int
    offset: int
    has_next: bool
    total_pages: int
    filters: FeedFilters
    freshness: FeedFreshness
    items: list[StockNewsItem]


class DisclosureFilters(FeedFilters):
    correction: Literal["all", "only", "exclude"]


class StockDisclosureItem(BaseModel):
    id: int
    title: str
    disclosure_type: str | None  # DART 보고서명(정정 접두어 포함 원문)
    rcept_no: str | None  # DART 접수번호(14자리). 원문 URL에서 읽는다
    disclosed_date: date | None  # 접수일(KST). 시각은 저장하지 않는다
    disclosed_at: str | None  # 접수일 00:00 KST(ISO 8601, 예: 2026-11-03T00:00:00+09:00). 시각으로 쓰지 말 것
    source_url: str | None  # DART 원문 뷰어 URL
    is_correction: bool  # [기재정정]·[첨부정정] 등 정정·추가 공시. 원본과 별개 공시다
    collected_at: str | None


class DisclosureListResponse(BaseModel):
    ticker: str
    company_name: str
    asset_type: Literal["stock", "etf"]
    total: int
    limit: int
    offset: int
    has_next: bool
    total_pages: int
    filters: DisclosureFilters
    supported: bool  # ETF는 공시 수집 대상이 아니라 false(items=[], message에 안내)
    message: str | None
    freshness: FeedFreshness
    items: list[StockDisclosureItem]


def _feed_company(db, ticker: str):
    """종목 해석은 엄격 모드(allow_fuzzy=False): 종목코드·정확한 종목명·공백/대소문자 차이·등록된 별칭만. 비슷한 이름으로
    다른 종목의 뉴스·공시를 보여주지 않는다. 못 찾으면 404 + 후보 안내 문장."""
    res = resolve_company(db, ticker, allow_fuzzy=False)
    if res.company is None:
        raise HTTPException(status_code=404, detail=res.message)
    return res.company


def _check_range(date_from: date | None, date_to: date | None) -> None:
    """거꾸로 된 날짜 범위는 FastAPI 기본 검증 오류와 같은 형식의 422(기록 API app/api/history_params와 같다)."""
    if date_from and date_to and date_from > date_to:
        raise RequestValidationError([{"type": "value_error", "loc": ("query", "date_from"),
                                       "msg": "date_from이 date_to보다 늦습니다.", "input": date_from.isoformat()}])


def _today() -> date:
    return datetime.now(stock_feed.KST).date()


@router.get("/{ticker}/news", response_model=NewsListResponse)
def get_stock_news(
    ticker: str = Path(min_length=1, max_length=50),
    limit: int = Query(stock_feed.DEFAULT_LIMIT, ge=1, le=stock_feed.MAX_LIMIT),
    offset: int = Query(0, ge=0, le=MAX_OFFSET),
    date_from: date | None = Query(None, description="이 날짜(KST)부터, YYYY-MM-DD"),
    date_to: date | None = Query(None, description="이 날짜(KST)까지, 그날 끝까지 포함. YYYY-MM-DD"),
    order: Literal["asc", "desc"] = "desc",
) -> dict:
    """종목 뉴스 목록(공개, 게시 시각 최신순 기본). 저장된 기사만이고 관련성 낮음으로 표시된 기사(is_relevant=false)는 뺀다."""
    _check_range(date_from, date_to)
    db = SessionLocal()
    try:
        company = _feed_company(db, ticker)
        return stock_feed.news_page(db, company, limit, offset, date_from, date_to, order, _today())
    finally:
        db.close()


@router.get("/{ticker}/disclosures", response_model=DisclosureListResponse)
def get_stock_disclosures(
    ticker: str = Path(min_length=1, max_length=50),
    limit: int = Query(stock_feed.DEFAULT_LIMIT, ge=1, le=stock_feed.MAX_LIMIT),
    offset: int = Query(0, ge=0, le=MAX_OFFSET),
    date_from: date | None = Query(None, description="이 접수일(KST)부터, YYYY-MM-DD"),
    date_to: date | None = Query(None, description="이 접수일(KST)까지 포함. YYYY-MM-DD"),
    order: Literal["asc", "desc"] = "desc",
    correction: Literal["all", "only", "exclude"] = Query("all", description="정정 공시: all(전부)·only(정정만)·exclude(정정 제외)"),
) -> dict:
    """종목 DART 공시 목록(공개, 접수일 최신순 기본). ETF는 200 + supported=false."""
    _check_range(date_from, date_to)
    db = SessionLocal()
    try:
        company = _feed_company(db, ticker)
        return stock_feed.disclosure_page(db, company, limit, offset, date_from, date_to, order, correction, _today())
    finally:
        db.close()
