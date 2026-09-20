from app.db.session import SessionLocal
from app.models import Company, StockPrice


def stock_tool(ticker: str, period_days: int = 1) -> dict:
    """지정한 종목(이름)의 최근 N일 주가(등락률/거래량 등)를 DB에서 조회한다."""
    db = SessionLocal()
    try:
        company = db.query(Company).filter(Company.name == ticker).first()
        if company is None:
            return {
                "ticker": ticker,
                "period_days": period_days,
                "found": False,
                "message": f"'{ticker}'는 company 테이블에 등록되지 않은 종목입니다. 데이터 없음.",
            }

        rows = (
            db.query(StockPrice)
            .filter(StockPrice.company_id == company.id)
            .order_by(StockPrice.price_date.desc())
            .limit(period_days)
            .all()
        )

        if not rows:
            return {
                "ticker": ticker,
                "period_days": period_days,
                "found": False,
                "message": f"'{ticker}'는 등록된 종목이지만 아직 수집된 주가 데이터가 없습니다.",
            }

        # 최근 날짜가 먼저 오도록 조회했으니, 응답은 날짜 오름차순으로 보기 좋게 정렬
        rows = list(reversed(rows))

        prices = [
            {
                "price_date": row.price_date.isoformat(),
                "close_price": float(row.close_price),
                "volume": row.volume,
                "change_pct": float(row.change_pct) if row.change_pct is not None else None,
            }
            for row in rows
        ]

        latest = prices[-1]

        return {
            "ticker": ticker,
            "period_days": period_days,
            "found": True,
            "change_pct": latest["change_pct"],
            "volume": latest["volume"],
            "prices": prices,
        }
    finally:
        db.close()
