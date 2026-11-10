"""GET /api/market/overview: 공개 시장 요약(코스피·코스닥 지수, 수집 대상 주식의 상승·하락·보합, 데이터 신선도). 2026-11-10(41차).

계산 규칙·한계는 app/market_snapshot.py 모듈 docstring. 로그인 불필요, 읽기 전용(쿼리 4개), LLM 없음.
일일 브리핑의 market 영역과 같은 저장 데이터를 쓰지만 브리핑을 만들지 않아도 된다.
"""

from datetime import date, datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app import market_snapshot
from app.db.session import get_db

router = APIRouter(prefix="/api/market", tags=["market"])


class IndexQuote(BaseModel):
    code: Literal["KS11", "KQ11"]
    name: str
    date: date | None  # 기준일(이 지수의 마지막 저장 종가 날짜). 없으면 이 항목의 값이 전부 null
    close: float | None
    previous_date: date | None  # 비교에 쓴 직전 저장 행의 날짜
    previous_close: float | None
    change: float | None  # 등락폭(포인트). 직전 행이 직전 거래일이 아니면 null
    change_pct: float | None  # 등락률(%), 소수 둘째 자리
    change_unavailable_reason: Literal["no_previous_row", "previous_trading_day_missing"] | None
    first_collected_at: str | None  # 이 행을 처음 저장한 시각(ISO 8601, KST)
    lag_business_days: int | None  # 기대 거래일 대비 늦은 KRX 거래일 수


class BreadthCounts(BaseModel):
    total: int  # 그날 시세 행이 있는 주식 수(= up + down + flat + change_missing)
    up: int
    down: int
    flat: int
    change_missing: int  # 시세는 있지만 등락률이 비어 있는 종목(상승·하락·보합에 넣지 않는다)
    no_price: int  # 등록된 주식 중 그날 시세 행이 없는 종목(total에 들지 않는다)
    registered: int  # 등록된(수집 대상) 주식 수
    up_ratio_pct: float | None  # up / (up + down + flat) x 100, 소수 첫째 자리. 분모가 0이면 null
    down_ratio_pct: float | None
    flat_ratio_pct: float | None


class Breadth(BreadthCounts):
    date: date
    universe: Literal["collected_stocks"]  # 시장 전체 종목이 아니라 수집 대상(시가총액 상위) 주식만, ETF 제외
    lag_business_days: int
    by_market: dict[str, BreadthCounts]  # "KOSPI" | "KOSDAQ"(코스닥 글로벌 포함) | "OTHER"(시장 미상)


class MarketFreshness(BaseModel):
    status: Literal["latest", "delayed", "stale", "no_data"]  # 일일 브리핑 freshness.status와 같은 기준
    lag_business_days: int | None  # 지수·상승하락 중 가장 늦은 것
    calendar_unknown_days: int  # 지연에 넣은 날 중 KRX 달력으로 확인할 수 없는 날 수
    intraday_excluded: bool  # 지금 장중이라 오늘 날짜 행을 쓰지 않았다
    dates_differ: bool  # 지수끼리 또는 지수와 상승하락의 기준일이 다르다


class LastAttempt(BaseModel):
    """refresh_data 단계의 마지막 실제 시도(skipped 제외). 수동 수집 스크립트는 기록하지 않아 더 오래된 값일 수 있다."""

    step: str
    status: Literal["success", "partial", "failed"]
    ok: bool  # status != "failed"
    finished_at: str | None  # ISO 8601, KST
    error_code: str | None  # 실패 분류 코드(메시지·URL은 저장하지 않는다)


class LastCollection(BaseModel):
    index: LastAttempt | None  # 지수 수집(refresh_data "index" 단계). 기록이 없으면 null
    prices: LastAttempt | None  # 종목 시세 수집("prices" 단계). 상승·하락 집계의 원천


class MarketSource(BaseModel):
    provider: str
    tables: list[str]
    per_row_source_recorded: bool  # 행마다 실제로 쓴 폴백 소스를 저장하지 않는다(false)


class MarketOverviewResponse(BaseModel):
    as_of: date | None  # 지수 기준일(두 지수 날짜가 다르면 더 이른 날). 지수가 하나도 없으면 null
    expected_date: date  # 지금 종가가 있어야 할 가장 최근 KRX 거래일
    generated_at: str  # 응답을 만든 시각(ISO 8601, KST)
    status: Literal["ok", "partial", "no_data"]
    missing: list[str]  # 없는 부분: "KS11", "KQ11", "breadth"
    freshness: MarketFreshness
    indices: list[IndexQuote]  # 항상 KS11, KQ11 순서로 2개(없으면 값이 null인 항목)
    breadth: Breadth | None
    last_collection: LastCollection  # 지연·결측이 수집 실패 때문인지 구분용(값을 바꾸지 않는다)
    source: MarketSource
    unavailable: list[str]  # 저장하지 않아 제공하지 않는 지표: "trading_value", "market_volume"
    note: str


def _now() -> datetime:
    """테스트가 바꿔 끼울 수 있게 함수로 둔다."""
    return datetime.now(timezone.utc)


@router.get("/overview", response_model=MarketOverviewResponse)
def get_market_overview(db: Session = Depends(get_db)) -> dict:
    """시장 요약(공개). 저장된 일별 종가만 쓰고 실시간 시세가 아니다. 데이터가 없어도 200 + status="no_data"."""
    return market_snapshot.load(db, _now())
