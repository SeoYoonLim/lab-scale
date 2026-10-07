from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import get_owner_key
from app.db.session import SessionLocal
from app.portfolio import InsufficientBalance, InsufficientQuantity, PriceUnavailable, get_portfolio, place_order
from app.tools.company_resolver import resolve_company

router = APIRouter(prefix="/api/portfolio", tags=["portfolio"])

# 한 번에 낼 수 있는 최대 주문 수량. 비현실적으로 큰 값으로 평균단가 계산이 깨지는 것을 막는 안전장치일 뿐,
# 실제 유동성/호가 제한을 흉내내지는 않는다.
MAX_ORDER_QUANTITY = 100_000


class OrderRequest(BaseModel):
    # 종목코드 또는 종목명(별칭/유사 종목명 포함). company_resolver로 해석한다.
    ticker: str = Field(min_length=1, max_length=50)
    side: Literal["buy", "sell"]
    quantity: int = Field(gt=0, le=MAX_ORDER_QUANTITY)


class HoldingItem(BaseModel):
    ticker: str
    company_name: str
    quantity: int
    avg_price: float
    # 상위 소스(네이버)와 DB 폴백 둘 다 실패하면 null이고, 그 종목은 평가손익 계산에서 빠진다(note 참고).
    current_price: float | None = None
    is_realtime: bool = False
    eval_amount: float | None = None
    profit_loss: float | None = None
    profit_loss_pct: float | None = None


class PortfolioResponse(BaseModel):
    cash_balance: float
    holdings: list[HoldingItem]
    total_eval_amount: float
    total_asset: float
    note: str | None = None


class HoldingAfterOrder(BaseModel):
    quantity: int
    avg_price: float


class OrderResponse(BaseModel):
    ticker: str
    company_name: str
    side: Literal["buy", "sell"]
    quantity: int
    price: float
    executed_at: str
    cash_balance: float
    # 매도로 전량 청산됐으면 null.
    holding: HoldingAfterOrder | None = None


@router.get("", response_model=PortfolioResponse)
def get_my_portfolio(owner_key: str = Depends(get_owner_key)) -> PortfolioResponse:
    """잔고 + 보유 종목(현재가/평가손익 포함). 사용자의 첫 호출이면 초기 잔고로 계좌가 자동 생성된다."""
    db = SessionLocal()
    try:
        return PortfolioResponse(**get_portfolio(db, owner_key))
    finally:
        db.close()


@router.post("/orders", response_model=OrderResponse)
def create_order(request: OrderRequest, owner_key: str = Depends(get_owner_key)) -> OrderResponse:
    """현재가로 즉시 체결되는 매수/매도 주문(호가 단위·장 시간 체크는 범위 밖)."""
    db = SessionLocal()
    try:
        res = resolve_company(db, request.ticker)
        if res.company is None:
            raise HTTPException(status_code=404, detail=res.message)

        try:
            result = place_order(db, owner_key, res.company, request.side, request.quantity)
        except PriceUnavailable as e:
            raise HTTPException(status_code=503, detail=str(e))
        except (InsufficientBalance, InsufficientQuantity) as e:
            raise HTTPException(status_code=400, detail=str(e))

        return OrderResponse(**result)
    finally:
        db.close()
