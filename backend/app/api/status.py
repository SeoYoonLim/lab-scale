from datetime import date

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.data_status import build_status
from app.db.session import get_db

router = APIRouter(prefix="/api", tags=["status"])


class LatestDate(BaseModel):
    latest_date: date | None


class MarketIndexStatus(BaseModel):
    latest_date: date | None
    stale: bool


class NewsStatus(BaseModel):
    latest_published_date: date | None


class Sources(BaseModel):
    stock_price: LatestDate
    etf_price: LatestDate
    market_index: MarketIndexStatus
    exchange_rate: LatestDate
    news: NewsStatus
    disclosure: LatestDate


class LastRefresh(BaseModel):
    finished_at: str | None
    ok: bool
    failed_steps: list[str]


class StatusResponse(BaseModel):
    generated_at: str
    sources: Sources
    last_refresh: LastRefresh | None
    is_realtime: bool
    note: str


@router.get("/status", response_model=StatusResponse)
def get_status(db: Session = Depends(get_db)) -> dict:
    """데이터 기준일(공개, 로그인 불필요). 화면 푸터 등에 "데이터 기준일"로 보여주는 용도다."""
    return build_status(db)
