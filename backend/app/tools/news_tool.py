from sqlalchemy import or_

from app.db.session import SessionLocal
from app.models import Company, News


def news_tool(company_name: str, limit: int = 5) -> dict:
    """지정한 종목(이름 또는 티커)의 최근 뉴스를 DB에서 조회한다."""
    db = SessionLocal()
    try:
        company = (
            db.query(Company)
            .filter(or_(Company.name == company_name, Company.ticker == company_name))
            .first()
        )
        if company is None:
            return {
                "company_name": company_name,
                "limit": limit,
                "found": False,
                "message": f"'{company_name}'는 company 테이블에 등록되지 않은 종목입니다. 데이터 없음.",
            }

        rows = (
            db.query(News)
            .filter(News.company_id == company.id)
            .order_by(News.published_at.desc())
            .limit(limit)
            .all()
        )

        if not rows:
            return {
                "company_name": company_name,
                "limit": limit,
                "found": False,
                "message": f"'{company_name}'는 등록된 종목이지만 아직 수집된 뉴스가 없습니다.",
            }

        news_items = [
            {
                "title": row.title,
                "source": row.source,
                "published_at": row.published_at.isoformat() if row.published_at else None,
                "url": row.url,
                "content": row.content,
            }
            for row in rows
        ]

        return {
            "company_name": company_name,
            "limit": limit,
            "found": True,
            "news": news_items,
        }
    finally:
        db.close()
