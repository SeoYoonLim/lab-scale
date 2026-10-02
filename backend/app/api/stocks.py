from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, HTTPException, Path
from pydantic import BaseModel

from app.db.session import SessionLocal
from app.realtime_price import get_realtime_price_data
from app.tools.company_resolver import resolve_company

router = APIRouter(prefix="/api/stocks", tags=["stocks"])


class RealtimePriceResponse(BaseModel):
    ticker: str
    company_name: str
    current_price: float
    # 상위 소스(네이버) 조회가 실패하거나 장중이 아니면 전일대비 금액은 모른다(폴백은 종가만 있음).
    change_amount: float | None = None
    change_pct: float | None = None
    # 가격의 기준 시각. 실시간이면 네이버가 체결 시각을, 폴백이면 저장된 종가의 날짜를 담는다.
    as_of: str
    # 이 API 응답을 만든 시각(서버 now, UTC). 캐싱된 값을 돌려줬어도 호출마다 새로 채운다.
    queried_at: str
    is_realtime: bool
    source: Literal["naver", "fallback"]
    # 입력이 별칭/유사 종목명이라 보정됐을 때만 채워진다(company_resolver 공통 동작).
    corrected_from: str | None = None


@router.get("/{ticker}/realtime-price", response_model=RealtimePriceResponse)
def get_stock_realtime_price(
    ticker: str = Path(min_length=1, max_length=50),
) -> RealtimePriceResponse:
    """종목 현재가(비공식 소스 기반 실시간 시세, 서버에서 종목별 3~5초 캐싱).

    상위 소스(네이버) 호출이 실패하거나 장중이 아니면 예외 대신 DB에 저장된 최근 종가로 폴백하고,
    `is_realtime=false`, `source="fallback"`로 표시한다. 비공식 소스라 언제든 막히거나 형식이
    바뀔 수 있다는 전제의 설계다(자세한 내용은 `app/realtime_price.py` 모듈 docstring 참고).
    """
    db = SessionLocal()
    try:
        res = resolve_company(db, ticker)
        if res.company is None:
            raise HTTPException(status_code=404, detail=res.message)
        company = res.company

        data = get_realtime_price_data(db, company)
        if data is None:
            raise HTTPException(
                status_code=503,
                detail=f"'{company.name}'의 시세를 가져올 수 없습니다. 실시간 소스와 저장된 주가 데이터가 모두 없습니다.",
            )

        extra = {"corrected_from": res.corrected_from} if res.corrected_from else {}
        return RealtimePriceResponse(
            ticker=company.ticker,
            company_name=company.name,
            queried_at=datetime.now(timezone.utc).isoformat(),
            **data,
            **extra,
        )
    finally:
        db.close()
