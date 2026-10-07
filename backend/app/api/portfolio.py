from typing import Any, Literal

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.api.deps import get_owner_key
from app.db.session import SessionLocal
from app.diagnosis import NoHoldings, diagnose
from app.portfolio import (
    InsufficientBalance,
    InsufficientQuantity,
    PriceUnavailable,
    get_portfolio,
    list_trades,
    place_order,
    reset_portfolio,
)
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


class TradeItem(BaseModel):
    id: int
    ticker: str
    company_name: str
    side: Literal["buy", "sell"]
    quantity: int
    price: float
    # trade 테이블에는 없는 값. 체결가 * 수량으로 계산한다.
    amount: float
    executed_at: str


class TradeList(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[TradeItem]


@router.get("", response_model=PortfolioResponse)
def get_my_portfolio(owner_key: str = Depends(get_owner_key)) -> PortfolioResponse:
    """잔고 + 보유 종목(현재가/평가손익 포함). 사용자의 첫 호출이면 초기 잔고로 계좌가 자동 생성된다."""
    db = SessionLocal()
    try:
        return PortfolioResponse(**get_portfolio(db, owner_key))
    finally:
        db.close()


@router.get("/trades", response_model=TradeList)
def get_my_trades(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    ticker: str | None = Query(None, min_length=1, max_length=50),
    owner_key: str = Depends(get_owner_key),
) -> TradeList:
    """내 체결 내역(최신순). `ticker`(종목코드 또는 종목명)를 주면 그 종목만. 체결이 없으면 total 0, items []."""
    db = SessionLocal()
    try:
        company_id = None
        if ticker is not None:
            res = resolve_company(db, ticker)
            if res.company is None:
                raise HTTPException(status_code=404, detail=res.message)
            company_id = res.company.id
        return TradeList(**list_trades(db, owner_key, limit, offset, company_id))
    finally:
        db.close()


@router.post("/reset", response_model=PortfolioResponse)
def reset_my_portfolio(
    body: Any = Body(default=None), owner_key: str = Depends(get_owner_key)
) -> PortfolioResponse:
    """내 모의투자를 처음 상태로 되돌린다: 보유 종목과 체결 내역 삭제 + 잔고를 초기값으로. **되돌릴 수 없다.**

    본문이 정확히 `{"confirm": true}`가 아니면(본문 없음 포함) 400이다. 관심종목/리서치 기록은 그대로다.
    """
    if not (isinstance(body, dict) and body.get("confirm") is True):
        raise HTTPException(status_code=400, detail='초기화하려면 본문에 {"confirm": true}를 보내야 합니다.')
    db = SessionLocal()
    try:
        reset_portfolio(db, owner_key)
        return PortfolioResponse(**get_portfolio(db, owner_key))
    finally:
        db.close()


class DiagnosisHolding(BaseModel):
    ticker: str
    company_name: str
    market: str
    quantity: int
    avg_price: float
    current_price: float | None = None
    is_realtime: bool
    # 현재가를 못 구한 종목. 평가금액/비중/손익이 null이고 합계에서 빠진다.
    price_unavailable: bool
    eval_amount: float | None = None
    weight_pct: float | None = None
    profit_loss: float | None = None
    profit_loss_pct: float | None = None
    return_20d_pct: float | None = None


class HoldingBrief(BaseModel):
    ticker: str
    company_name: str
    profit_loss_pct: float | None = None


class DiagnosisMetrics(BaseModel):
    total_asset: float
    cash_balance: float
    cash_weight_pct: float | None = None
    stock_eval_amount: float
    holding_count: int
    priced_holding_count: int
    top1_weight_pct: float | None = None
    top3_weight_pct: float | None = None
    herfindahl_index: float | None = None
    market_weights_pct: dict[str, float | None]
    total_profit_loss: float
    total_profit_loss_pct: float | None = None
    best_holding: HoldingBrief | None = None
    worst_holding: HoldingBrief | None = None


class DiagnosisFlag(BaseModel):
    code: str
    message: str
    value: float
    threshold: float
    ticker: str | None = None
    company_name: str | None = None
    market: str | None = None


class DiagnosisResponse(BaseModel):
    generated_at: str
    source: Literal["llm", "rule_based"]
    model: str | None = None
    metrics: DiagnosisMetrics
    holdings: list[DiagnosisHolding]
    flags: list[DiagnosisFlag]
    summary: str
    strengths: list[str]
    risks: list[str]
    suggestions: list[str]
    notes: list[str]
    disclaimer: str


@router.post("/diagnosis", response_model=DiagnosisResponse, response_model_exclude_none=False)
def diagnose_my_portfolio(owner_key: str = Depends(get_owner_key)) -> DiagnosisResponse:
    """내 포트폴리오 AI 진단. 지표와 플래그는 코드가 계산하고 LLM은 설명만 한다(실패 시 규칙 기반 문장).

    보유 종목이 없으면 400(LLM 호출 없음). 결과는 저장하지 않는다. LLM을 쓰면 수 초~수십 초 걸린다.
    """
    db = SessionLocal()
    try:
        return DiagnosisResponse(**diagnose(db, owner_key))
    except NoHoldings as e:
        raise HTTPException(status_code=400, detail=str(e))
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
