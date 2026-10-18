"""종목의 최근 N거래일 주가 시계열 조회(공용)와 차트용 응답 구성.

- `recent_price_rows`: stock_price에서 해당 종목의 최근 limit개 행을 최신순으로 읽는 읽기 전용 쿼리 1회.
  리서치 챗의 stock_tool과 공개 API(GET /api/stocks/{ticker}/price-history)가 같은 함수를 쓴다.
- `build_price_history`: 위 행으로 API 응답 dict를 만드는 순수 함수(DB 접근 없음).

days는 달력 일이 아니라 거래일(그 종목의 stock_price 행 수) 기준이다.
"""

from sqlalchemy.orm import Session

from app.models import StockPrice


def recent_price_rows(db: Session, company_id: int, limit: int) -> list[StockPrice]:
    """해당 종목의 최근 limit개 주가 행. 최신 날짜가 먼저 온다(읽기 전용, 쿼리 1회)."""
    return (
        db.query(StockPrice)
        .filter(StockPrice.company_id == company_id)
        .order_by(StockPrice.price_date.desc())
        .limit(limit)
        .all()
    )


def _num(value) -> float | None:
    return float(value) if value is not None else None


def build_price_history(company, rows_desc: list, days: int) -> dict:
    """최신순 주가 행(rows_desc)으로 차트용 응답을 만든다. items는 날짜 오름차순.

    행이 days보다 적으면 있는 만큼만 담고(count로 알 수 있다), 행이 없으면 items=[], price_as_of=None이다.
    rows_desc가 days보다 길어도 최근 days개만 쓴다. 값이 없는 칸은 None."""
    items = [
        {
            "date": row.price_date,
            "close": _num(row.close_price),
            "volume": row.volume,
            "change_pct": _num(row.change_pct),
            "open": _num(row.open_price),
            "high": _num(row.high_price),
            "low": _num(row.low_price),
        }
        for row in reversed(rows_desc[:days])
    ]
    return {
        "ticker": company.ticker,
        "company_name": company.name,
        "asset_type": company.asset_type,
        "days": days,
        "count": len(items),
        "price_as_of": items[-1]["date"] if items else None,
        "items": items,
    }
