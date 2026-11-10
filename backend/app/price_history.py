"""종목의 최근 N거래일 주가 시계열 조회(공용)와 차트용 응답 구성.

- `recent_price_rows`: stock_price에서 해당 종목의 최근 limit개 행을 최신순으로 읽는 읽기 전용 쿼리 1회.
  리서치 챗의 stock_tool과 공개 API(GET /api/stocks/{ticker}/price-history)가 같은 함수를 쓴다.
- `build_price_history`: 위 행으로 API 응답 dict를 만드는 순수 함수(DB 접근 없음).

days는 달력 일이 아니라 거래일(그 종목의 stock_price 행 수) 기준이다.

2026-11-10(41차): API의 days 상한을 90에서 365로 올렸다. 90은 성능 제한이 아니라 데이터가 2026-06-26부터만 있던 때의 한계였다
(지금은 2023-10-30부터 733거래일, 350종목 중 342종목이 365행 이상). 같은 (company_id, price_date) 인덱스로 최근 N행만 읽어
365행도 쿼리 1회·응답 약 40KB다. 데이터가 모자란 종목을 화면이 알 수 있게 응답에 available_count(그 종목의 저장 행 전체 수)·
first_date(가장 이른 저장 날짜)·missing_trading_days(응답 구간 안에서 KRX 달력상 거래일인데 행이 없는 날 수)를 더했다.
종가는 수집 소스(FinanceDataReader)가 준 값 그대로다. 소스가 수정주가로 준 구간이 섞여 있을 수 있다(예: 호가 단위에 맞지 않는
종가). 행마다 수정 여부를 저장하지 않아 구분할 수 없다(docs/FRONTEND_HANDOFF_SPRINT_41.md).
"""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import krx_calendar
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


def history_rows(db: Session, company_id: int, limit: int) -> tuple[list, int, object]:
    """API용: 최근 limit개 행(최신순) + 그 종목의 저장 행 전체 수 + 가장 이른 날짜를 쿼리 1회로 읽는다.
    창 함수(count·min OVER ())는 LIMIT 전에 계산되므로 전체 행 기준이다. 행이 없으면 ([], 0, None)."""
    total = func.count().over().label("available_count")
    first = func.min(StockPrice.price_date).over().label("first_date")
    rows = db.execute(
        select(StockPrice, total, first)
        .where(StockPrice.company_id == company_id)
        .order_by(StockPrice.price_date.desc())
        .limit(limit)
    ).all()
    if not rows:
        return [], 0, None
    return [r[0] for r in rows], int(rows[0][1]), rows[0][2]


def missing_trading_days(dates: list) -> int:
    """오름차순 날짜 목록의 첫날~끝날 사이에서 KRX 달력이 거래일로 확정한 날 중 행이 없는 날 수(미확인 평일은 세지 않는다)."""
    if len(dates) < 2:
        return 0
    have = set(dates)
    return sum(1 for d in krx_calendar.trading_days(dates[0], dates[-1], include_unknown=False) if d not in have)


def price_rows_between(db: Session, company_id: int, start, end) -> list[StockPrice]:
    """start~end(포함)의 주가 행. 오래된 날짜가 먼저 온다(읽기 전용, 쿼리 1회)."""
    return (
        db.query(StockPrice)
        .filter(StockPrice.company_id == company_id, StockPrice.price_date >= start, StockPrice.price_date <= end)
        .order_by(StockPrice.price_date)
        .all()
    )


def last_row_before(db: Session, company_id: int, day) -> StockPrice | None:
    """day 직전 거래일의 주가 행. 없으면 None(읽기 전용, 쿼리 1회)."""
    return (
        db.query(StockPrice)
        .filter(StockPrice.company_id == company_id, StockPrice.price_date < day)
        .order_by(StockPrice.price_date.desc())
        .first()
    )


def price_range(db: Session, company_id: int):
    """해당 종목 시세의 (첫 날짜, 마지막 날짜). 시세가 없으면 (None, None)."""
    return (
        db.query(func.min(StockPrice.price_date), func.max(StockPrice.price_date))
        .filter(StockPrice.company_id == company_id)
        .one()
    )


def market_has_rows(db: Session, day) -> bool:
    """그날 시세가 있는 종목이 하나라도 있는지. 없으면 휴장일로 본다(KRX 휴장일 캘린더가 없다)."""
    return db.query(StockPrice.id).filter(StockPrice.price_date == day).first() is not None


def _num(value) -> float | None:
    return float(value) if value is not None else None


def build_price_history(company, rows_desc: list, days: int, available_count: int | None = None, first_date=None) -> dict:
    """최신순 주가 행(rows_desc)으로 차트용 응답을 만든다. items는 날짜 오름차순.

    행이 days보다 적으면 있는 만큼만 담고(count로 알 수 있다), 행이 없으면 items=[], price_as_of=None이다.
    rows_desc가 days보다 길어도 최근 days개만 쓴다. 값이 없는 칸은 None.
    available_count·first_date를 모르면(호출자가 안 주면) rows_desc로 채운다(rows_desc가 전체 행이라고 보는 것)."""
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
        "available_count": len(rows_desc) if available_count is None else available_count,
        "first_date": first_date if first_date is not None else (rows_desc[-1].price_date if rows_desc else None),
        "missing_trading_days": missing_trading_days([i["date"] for i in items]),
        "items": items,
    }
