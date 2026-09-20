from sqlalchemy import or_

from app.db.session import SessionLocal
from app.models import Company, Disclosure


def disclosure_tool(company_name: str, limit: int = 5) -> dict:
    """지정한 종목(이름 또는 티커)의 최근 공시(DART)를 DB에서 조회한다."""
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
            db.query(Disclosure)
            .filter(Disclosure.company_id == company.id)
            .order_by(Disclosure.disclosed_at.desc())
            .limit(limit)
            .all()
        )

        if not rows:
            return {
                "company_name": company_name,
                "limit": limit,
                "found": False,
                "message": f"'{company_name}'는 등록된 종목이지만 아직 수집된 공시가 없습니다.",
            }

        disclosure_items = [
            {
                "title": row.title,
                "disclosure_type": row.disclosure_type,
                "disclosed_at": row.disclosed_at.isoformat() if row.disclosed_at else None,
                "source_url": row.source_url,
            }
            for row in rows
        ]

        return {
            "company_name": company_name,
            "limit": limit,
            "found": True,
            "disclosures": disclosure_items,
        }
    finally:
        db.close()
