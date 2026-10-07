from fastapi import APIRouter, Depends, HTTPException, Path, Response
from pydantic import BaseModel, Field

from app.api.deps import get_owner_key
from app.db.session import SessionLocal
from app.tools.company_resolver import resolve_company
from app.watchlist import add_item, list_items, remove_item

router = APIRouter(prefix="/api/watchlist", tags=["watchlist"])

MAX_TICKER_LEN = 50


class WatchlistCreate(BaseModel):
    # 종목코드 또는 종목명(별칭/유사 종목명 포함). /api/stocks/{ticker}와 같은 company_resolver를 쓴다.
    ticker: str = Field(min_length=1, max_length=MAX_TICKER_LEN)


class WatchlistItem(BaseModel):
    company_id: int
    ticker: str
    company_name: str
    added_at: str
    # 실시간이 아니라 DB에 저장된 가장 최근 종가(일별). 실시간 시세는 /api/stocks/{ticker}/realtime-price를 쓴다.
    latest_close: float | None = None
    latest_close_date: str | None = None
    change_pct: float | None = None


class WatchlistList(BaseModel):
    items: list[WatchlistItem]


@router.get("", response_model=WatchlistList)
def get_watchlist(owner_key: str = Depends(get_owner_key)) -> WatchlistList:
    """로그인 사용자의 관심종목 전체(등록 최신순)."""
    db = SessionLocal()
    try:
        return WatchlistList(items=list_items(db, owner_key))
    finally:
        db.close()


@router.post("", response_model=WatchlistItem, status_code=201)
def add_watchlist_item(request: WatchlistCreate, owner_key: str = Depends(get_owner_key)) -> WatchlistItem:
    """관심종목에 종목을 추가한다. 이미 등록돼 있으면 409."""
    db = SessionLocal()
    try:
        res = resolve_company(db, request.ticker)
        if res.company is None:
            raise HTTPException(status_code=404, detail=res.message)

        item, already_existed = add_item(db, owner_key, res.company.id)
        if already_existed:
            raise HTTPException(status_code=409, detail=f"'{res.company.name}'는 이미 관심종목에 등록되어 있습니다.")

        return WatchlistItem(
            company_id=res.company.id,
            ticker=res.company.ticker,
            company_name=res.company.name,
            added_at=item.created_at.isoformat(),
        )
    finally:
        db.close()


@router.delete("/{ticker}", status_code=204)
def delete_watchlist_item(
    ticker: str = Path(min_length=1, max_length=MAX_TICKER_LEN),
    owner_key: str = Depends(get_owner_key),
) -> Response:
    """관심종목에서 종목을 뺀다. 등록돼 있지 않으면(종목 자체를 못 찾는 경우 포함) 404."""
    db = SessionLocal()
    try:
        res = resolve_company(db, ticker)
        if res.company is None:
            raise HTTPException(status_code=404, detail=res.message)

        if not remove_item(db, owner_key, res.company.id):
            raise HTTPException(status_code=404, detail=f"'{res.company.name}'는 관심종목에 등록되어 있지 않습니다.")
        return Response(status_code=204)
    finally:
        db.close()
