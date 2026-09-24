from app.db.session import SessionLocal
from app.models import News
from app.tools.company_resolver import not_found_response, resolve_company, to_int


def news_tool(company_name: str, limit: int = 5) -> dict:
    """지정한 종목(이름 또는 티커)의 최근 뉴스를 DB에서 조회한다."""
    limit = to_int(limit, default=5)
    db = SessionLocal()
    try:
        res = resolve_company(db, company_name)
        if res.company is None:
            return not_found_response({"company_name": company_name, "limit": limit}, res)
        company = res.company
        corrected = {"corrected_from": res.corrected_from} if res.corrected_from else {}

        rows = (
            db.query(News)
            .filter(News.company_id == company.id)
            .order_by(News.published_at.desc())
            .limit(limit)
            .all()
        )

        if not rows:
            return {
                "company_name": company.name,
                "limit": limit,
                "found": False,
                "message": f"'{company.name}'는 등록된 종목이지만 아직 수집된 뉴스가 없습니다.",
                **corrected,
            }

        news_items = [
            {
                "company_name": company.name,
                "title": row.title,
                "source": row.source,
                "published_at": row.published_at.isoformat() if row.published_at else None,
                "url": row.url,
                "content": row.content,
            }
            for row in rows
        ]

        return {
            "company_name": company.name,
            "limit": limit,
            "found": True,
            "news": news_items,
            **corrected,
        }
    finally:
        db.close()
