from app.db.session import SessionLocal
from app.models import ExchangeRate
from app.tools.company_resolver import to_int

# 1차 범위는 USD/KRW만(FR-07, README 참고). 종목처럼 company_resolver로 찾을 대상이 없어 ticker 인자가 없다.
PAIR_CODE = "USD/KRW"
PAIR_NAME = "원/달러"


def fx_tool(period_days: int = 5) -> dict:
    """최근 N거래일 원/달러(USD/KRW) 환율 추이(현재가, 전일대비 등락률, 기간 추세)를 DB에서 조회한다."""
    period_days = to_int(period_days, default=5, hi=60)
    db = SessionLocal()
    try:
        rows = (
            db.query(ExchangeRate)
            .filter(ExchangeRate.pair_code == PAIR_CODE)
            .order_by(ExchangeRate.price_date.desc())
            .limit(period_days)
            .all()
        )
        if not rows:
            return {
                "pair_name": PAIR_NAME,
                "period_days": period_days,
                "found": False,
                "message": "환율 데이터가 아직 수집되지 않았습니다.",
            }

        rows = list(reversed(rows))  # 날짜 오름차순으로 보기 좋게 정렬(stock_tool과 동일한 관례)
        rates = [
            {
                "price_date": r.price_date.isoformat(),
                "close_rate": float(r.close_price),
                "change_pct": float(r.change_pct) if r.change_pct is not None else None,
            }
            for r in rows
        ]
        latest = rates[-1]

        period_change_pct = None
        if len(rates) > 1:
            period_change_pct = round((rates[-1]["close_rate"] / rates[0]["close_rate"] - 1) * 100, 2)
        if period_change_pct is None:
            trend = "알수없음"
        elif period_change_pct > 0:
            trend = "상승"
        elif period_change_pct < 0:
            trend = "하락"
        else:
            trend = "보합"

        return {
            "pair_name": PAIR_NAME,
            "period_days": len(rates),
            "found": True,
            "latest_date": latest["price_date"],
            "latest_rate": latest["close_rate"],
            "daily_change_pct": latest["change_pct"],
            "period_change_pct": period_change_pct,
            "trend": trend,
            "rates": rates,
        }
    finally:
        db.close()
