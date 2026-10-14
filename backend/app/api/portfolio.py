from typing import Any, Literal

from fastapi import APIRouter, Body, Depends, HTTPException, Path, Query, Response
from pydantic import BaseModel, Field

import app.diagnosis_history as diagnosis_history
from app.api.deps import get_current_user, get_owner_key
from app.api.research import BIGINT_MAX
from app.auth import CurrentUser
from app.db.session import SessionLocal
from app.diagnosis import NoHoldings, diagnose
from app.disclaimer import DISCLAIMER
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


class DiagnosisBody(BaseModel):
    """진단 응답 본문. 저장본(portfolio_diagnosis.result)도 이 형태 그대로다."""

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


class DiagnosisResponse(DiagnosisBody):
    # 저장에 실패하면 둘 다 null. 진단 결과는 저장 성공 여부와 무관하게 정상 반환된다.
    diagnosis_id: int | None = None
    created_at: str | None = None


class DiagnosisDetail(DiagnosisBody):
    id: int
    created_at: str


class DiagnosisFlagBrief(BaseModel):
    code: str
    ticker: str | None = None
    company_name: str | None = None
    market: str | None = None


class DiagnosisHistoryItem(BaseModel):
    id: int
    created_at: str
    source: Literal["llm", "rule_based"]
    total_asset: float
    holdings_count: int
    flags: list[DiagnosisFlagBrief]
    summary_preview: str


class DiagnosisHistoryList(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[DiagnosisHistoryItem]


class TotalAssetChange(BaseModel):
    previous: float
    current: float
    change_amount: float
    change_pct: float | None = None


class HoldingWeightChange(BaseModel):
    ticker: str
    company_name: str
    # added: 이전 진단에 없던 종목(신규 편입), removed: 이번 진단에 없는 종목(전량 제외), kept: 둘 다 보유
    status: Literal["added", "removed", "kept"]
    previous_weight_pct: float | None = None
    current_weight_pct: float | None = None
    weight_change_pp: float | None = None


class ReturnChange(BaseModel):
    ticker: str
    company_name: str
    previous_return_20d_pct: float
    current_return_20d_pct: float
    change_pp: float


class FlagChanges(BaseModel):
    added: list[DiagnosisFlag]
    resolved: list[DiagnosisFlag]


class DiagnosisChanges(BaseModel):
    total_asset: TotalAssetChange
    holdings: list[HoldingWeightChange]
    flags: FlagChanges
    returns_20d: list[ReturnChange]


class DiagnosisComparison(BaseModel):
    current: DiagnosisHistoryItem
    previous: DiagnosisHistoryItem | None = None
    changes: DiagnosisChanges | None = None
    disclaimer: str = DISCLAIMER


def _diagnosis_not_found(diagnosis_id: int) -> HTTPException:
    # 남의 진단도 "없음"과 똑같이 404로 응답한다(403이면 그 id가 존재한다는 사실이 드러난다).
    return HTTPException(status_code=404, detail=f"diagnosis_id={diagnosis_id} 진단을 찾을 수 없습니다.")


@router.post("/diagnosis", response_model=DiagnosisResponse, response_model_exclude_none=False)
def diagnose_my_portfolio(user: CurrentUser = Depends(get_current_user)) -> DiagnosisResponse:
    """내 포트폴리오 AI 진단. 지표와 플래그는 코드가 계산하고 LLM은 설명만 한다(실패 시 규칙 기반 문장).

    보유 종목이 없으면 400(LLM 호출 없음, 저장 안 함). 결과는 사용자당 최근 50건까지 저장하고 `diagnosis_id`/`created_at`을
    붙인다(저장에 실패하면 null이고 진단은 정상 반환). LLM을 쓰면 수 초~수십 초 걸린다.
    """
    db = SessionLocal()
    try:
        body = DiagnosisBody(**diagnose(db, user.owner_key))
    except NoHoldings as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        db.close()
    saved = diagnosis_history.save_diagnosis(user.id, body.model_dump(mode="json"))
    return DiagnosisResponse(
        **body.model_dump(),
        diagnosis_id=saved["id"] if saved else None,
        created_at=saved["created_at"] if saved else None,
    )


# /diagnosis/history는 /diagnosis/{diagnosis_id}보다 먼저 정의한다(그렇지 않으면 "history"가 id 자리에 들어간다).
@router.get("/diagnosis/history", response_model=DiagnosisHistoryList)
def list_my_diagnoses(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    user: CurrentUser = Depends(get_current_user),
) -> DiagnosisHistoryList:
    """내 진단 이력(최신순, 최근 50건까지 보관). 이력이 없으면 total 0, items []."""
    total, items = diagnosis_history.list_diagnoses(user.id, limit, offset)
    return DiagnosisHistoryList(total=total, limit=limit, offset=offset, items=items)


@router.get("/diagnosis/{diagnosis_id}", response_model=DiagnosisDetail, response_model_exclude_none=False)
def get_my_diagnosis(
    diagnosis_id: int = Path(ge=1, le=BIGINT_MAX), user: CurrentUser = Depends(get_current_user)
) -> DiagnosisDetail:
    """내 진단 한 건(저장된 진단 응답 그대로 + id, created_at)."""
    record = diagnosis_history.get_diagnosis(user.id, diagnosis_id)
    if record is None:
        raise _diagnosis_not_found(diagnosis_id)
    return DiagnosisDetail(**record["result"], id=record["id"], created_at=record["created_at"].isoformat())


@router.get("/diagnosis/{diagnosis_id}/compare", response_model=DiagnosisComparison)
def compare_my_diagnosis(
    diagnosis_id: int = Path(ge=1, le=BIGINT_MAX),
    base: int | None = Query(None, ge=1, le=BIGINT_MAX),
    user: CurrentUser = Depends(get_current_user),
) -> DiagnosisComparison:
    """내 진단과 이전 진단의 비교(코드로만 계산, LLM 없음). `base`가 없으면 바로 이전 진단과 비교한다."""
    current = diagnosis_history.get_diagnosis(user.id, diagnosis_id)
    if current is None:
        raise _diagnosis_not_found(diagnosis_id)
    if base is None:
        previous = diagnosis_history.get_previous_diagnosis(user.id, current)
    else:
        previous = diagnosis_history.get_diagnosis(user.id, base)
        if previous is None:
            raise _diagnosis_not_found(base)
    return DiagnosisComparison(**diagnosis_history.build_comparison(current, previous))


@router.delete("/diagnosis/{diagnosis_id}", status_code=204)
def delete_my_diagnosis(
    diagnosis_id: int = Path(ge=1, le=BIGINT_MAX), user: CurrentUser = Depends(get_current_user)
) -> Response:
    """내 진단 한 건을 삭제한다."""
    if not diagnosis_history.delete_diagnosis(user.id, diagnosis_id):
        raise _diagnosis_not_found(diagnosis_id)
    return Response(status_code=204)


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
