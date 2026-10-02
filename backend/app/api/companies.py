from fastapi import APIRouter, Query
from pydantic import BaseModel

from app.companies import search_companies
from app.db.session import SessionLocal

router = APIRouter(prefix="/api/companies", tags=["companies"])

MAX_Q_LEN = 100


class CompanyItem(BaseModel):
    ticker: str
    name: str
    market: str | None = None
    sector: str | None = None
    # DB에 저장된 가장 최근 종가(일별, 실시간 아님). 실시간 시세는 /api/stocks/{ticker}/realtime-price를 쓴다.
    latest_close: float | None = None
    change_pct: float | None = None


@router.get("", response_model=list[CompanyItem])
def get_companies(
    q: str | None = Query(
        None, max_length=MAX_Q_LEN, description="종목명 또는 티커 부분 일치 검색어(대소문자/공백 무시, 별칭 포함). 없으면 상위 limit개"
    ),
    limit: int = Query(50, ge=1, le=300),
) -> list[CompanyItem]:
    """FR-02로 수집된 company 데이터 검색(+최근 종가/등락률). 프론트 '종목' 페이지가 직접 호출한다."""
    db = SessionLocal()
    try:
        return [CompanyItem(**item) for item in search_companies(db, q, limit)]
    finally:
        db.close()
