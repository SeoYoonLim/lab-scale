"""GET /api/etf/{ticker}/holdings: ETF 구성종목과 비중(운용사 공시 PDF를 수집해 저장한 값, 공개).

인증 정책: 시세·사건 반응 같은 공개 시장 데이터 API(/api/stocks/{ticker}/...)와 같이 공개다. 사용자 데이터가 없다.
없는 종목은 404 + 후보 안내(resolve_company, 공개 시세 API와 같은 방식), ETF가 아닌 종목은 404, ETF인데 데이터가 없으면 200 +
items=[] + message(주가가 아직 없을 때의 price-history와 같은 방식)다. 읽기 전용이고 쿼리는 종목 해석을 빼고 3개다.
"""

from fastapi import APIRouter, HTTPException, Path, Query
from pydantic import BaseModel

from app.db.session import SessionLocal
from app.etf_holdings import DEFAULT_LIMIT, MAX_LIMIT, not_etf_message
from app.etf_holdings_db import holdings_view
from app.tools.company_resolver import resolve_company

router = APIRouter(prefix="/api/etf", tags=["etf"])


class HoldingItem(BaseModel):
    rank: int
    name: str
    code: str | None
    weight_pct: float


class DataWarning(BaseModel):
    kind: str
    message: str


class HoldingsResponse(BaseModel):
    ticker: str
    name: str
    asset_type: str
    as_of_date: str | None
    collected_at: str | None
    stale: bool
    stale_notice: str | None
    source: str | None
    holding_count: int
    total_weight_pct: float | None
    top_weight_pct: float | None
    limit: int
    items: list[HoldingItem]
    data_warnings: list[DataWarning]
    message: str | None
    disclaimer: str


@router.get("/{ticker}/holdings", response_model=HoldingsResponse, response_model_exclude_none=False)
def get_etf_holdings(
    ticker: str = Path(min_length=1, max_length=50),
    limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT, description="비중이 큰 순서로 보여줄 구성종목 수"),
) -> dict:
    """가장 최근 기준일의 구성종목을 비중 큰 순서로 최대 limit개(현금성 자산 제외). 공개. 한계와 필드는 API.md 참고."""
    db = SessionLocal()
    try:
        res = resolve_company(db, ticker)
        if res.company is None:
            raise HTTPException(status_code=404, detail=res.message)
        if res.company.asset_type != "etf":
            raise HTTPException(status_code=404, detail=not_etf_message(res.company.name))
        return holdings_view(db, res.company, limit)
    finally:
        db.close()
