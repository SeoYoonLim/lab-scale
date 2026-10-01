from app.db.session import SessionLocal
from app.discovery import get_trending


def discovery_tool(category: str = "gainers", limit: int = 10) -> dict:
    """특정 종목을 지정하지 않고 시장 전체에서 오늘(최근 거래일) 기준 급등/급락/거래량 급증 종목을 찾는다."""
    db = SessionLocal()
    try:
        return get_trending(db, category, limit)
    finally:
        db.close()
