from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.db.session import SessionLocal
from app.discovery import get_trending

router = APIRouter(prefix="/api/discovery", tags=["discovery"])

Category = Literal["gainers", "losers", "volume_surge"]


class TrendingItem(BaseModel):
    ticker: str
    company_name: str
    close_price: float
    change_pct: float | None = None
    volume: int | None = None
    # 직전 window_days 거래일(당일 제외) 평균 거래량과, 당일 거래량이 그 평균의 몇 배인지.
    avg_volume: float | None = None
    volume_ratio: float | None = None


class TrendingResponse(BaseModel):
    category: Category
    price_date: str
    window_days: int
    items: list[TrendingItem]


@router.get("/trending", response_model=TrendingResponse)
def get_trending_stocks(
    category: Category = Query("gainers", description="gainers(급등)/losers(급락)/volume_surge(거래량 급증)"),
    limit: int = Query(10, ge=1, le=50),
) -> TrendingResponse:
    """특정 종목 질문 없이 바로 호출하는 시장 전체 스크리닝(FR-13). stock_price만으로 계산하는 순수 집계다."""
    db = SessionLocal()
    try:
        result = get_trending(db, category, limit)
        if not result["found"]:
            raise HTTPException(status_code=503, detail=result["message"])
        return TrendingResponse(**result)
    finally:
        db.close()
