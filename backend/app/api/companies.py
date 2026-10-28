from typing import Literal

from fastapi import APIRouter, Query, Response
from pydantic import BaseModel

from app.companies import search_companies_page
from app.db.session import SessionLocal
from app.pagination import MAX_OFFSET

router = APIRouter(prefix="/api/companies", tags=["companies"])

MAX_Q_LEN = 100


class CompanyItem(BaseModel):
    ticker: str
    name: str
    market: str | None = None
    sector: str | None = None
    # 'stock'(주식) | 'etf'. ETF는 market/sector가 null이다.
    asset_type: Literal["stock", "etf"] = "stock"
    # DB에 저장된 가장 최근 종가(일별, 실시간 아님). 실시간 시세는 /api/stocks/{ticker}/realtime-price를 쓴다.
    latest_close: float | None = None
    change_pct: float | None = None


@router.get("", response_model=list[CompanyItem])
def get_companies(
    q: str | None = Query(
        None, max_length=MAX_Q_LEN, description="종목명 또는 티커 부분 일치 검색어(대소문자/공백 무시, 별칭 포함). 없으면 상위 limit개"
    ),
    limit: int = Query(50, ge=1, le=300),
    asset_type: Literal["stock", "etf"] | None = Query(None, description="stock(주식)/etf만. 생략하면 전체"),
    offset: int = Query(0, ge=0, le=MAX_OFFSET, description="건너뛸 건수. 전체 매칭 건수 이상이면 빈 배열(200)"),
    sort: Literal["id", "name", "ticker"] = Query("id", description="정렬 기준. 기본 id는 수집 순서(예전과 같음)"),
    order: Literal["asc", "desc"] = Query("asc", description="정렬 방향"),
    response: Response = None,
) -> list[CompanyItem]:
    """FR-02로 수집된 company 데이터 검색(+최근 종가/등락률). 프론트 '종목' 페이지가 직접 호출한다.

    응답 본문은 배열 그대로이고, q/asset_type에 맞는 전체 건수는 `X-Total-Count` 헤더로 내려준다(limit/offset과 무관)."""
    db = SessionLocal()
    try:
        total, items = search_companies_page(db, q, limit, asset_type, offset, sort, order)
        response.headers["X-Total-Count"] = str(total)
        return [CompanyItem(**item) for item in items]
    finally:
        db.close()
