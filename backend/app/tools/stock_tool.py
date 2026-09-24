from app.db.session import SessionLocal
from app.models import StockPrice
from app.tools.company_resolver import not_found_response, resolve_company, to_int


def stock_tool(ticker: str, period_days: int = 1) -> dict:
    """지정한 종목(이름 또는 티커)의 최근 N일 주가(등락률/거래량 등)를 DB에서 조회한다."""
    period_days = to_int(period_days, default=1, hi=60)
    db = SessionLocal()
    try:
        res = resolve_company(db, ticker)
        if res.company is None:
            return not_found_response({"ticker": ticker, "period_days": period_days}, res)
        company = res.company
        corrected = {"corrected_from": res.corrected_from} if res.corrected_from else {}

        rows = (
            db.query(StockPrice)
            .filter(StockPrice.company_id == company.id)
            .order_by(StockPrice.price_date.desc())
            .limit(period_days)
            .all()
        )

        if not rows:
            return {
                "ticker": company.ticker,
                "company_name": company.name,
                "period_days": period_days,
                "found": False,
                "message": f"'{company.name}'는 등록된 종목이지만 아직 수집된 주가 데이터가 없습니다.",
                **corrected,
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
            "ticker": company.ticker,
            "company_name": company.name,
            "period_days": period_days,
            "found": True,
            "change_pct": latest["change_pct"],
            "volume": latest["volume"],
            "prices": prices,
            **corrected,
        }
    finally:
        db.close()
