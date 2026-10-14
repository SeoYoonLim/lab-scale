from app.db.session import SessionLocal
from app.korean import josa
from app.models import Disclosure
from app.tools.company_resolver import not_found_response, resolve_company, to_int


def disclosure_tool(company_name: str, limit: int = 5) -> dict:
    """지정한 종목(이름 또는 티커)의 최근 공시(DART)를 DB에서 조회한다."""
    limit = to_int(limit, default=5)
    db = SessionLocal()
    try:
        res = resolve_company(db, company_name)
        if res.company is None:
            return not_found_response({"company_name": company_name, "limit": limit}, res)
        company = res.company
        corrected = {"corrected_from": res.corrected_from} if res.corrected_from else {}

        if company.asset_type == "etf":
            # ETF는 개별 기업이 아니라 DART 공시 수집 대상이 아니다. "아직 수집된 공시가 없다"고 하면 곧 생길 것처럼 읽혀서
            # 왜 없는지를 결과에 담는다.
            return {
                "company_name": company.name,
                "limit": limit,
                "found": False,
                "asset_type": "etf",
                "message": f"{josa(company.name, '은/는')} ETF(상장지수펀드)입니다. ETF는 개별 기업이 아니라서 이 서비스는 "
                "ETF의 공시를 수집하지 않습니다. 공시·실적 대신 가격 흐름과 관련 뉴스로 확인할 수 있습니다.",
                **corrected,
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
                "company_name": company.name,
                "limit": limit,
                "found": False,
                "message": f"'{company.name}'는 등록된 종목이지만 아직 수집된 공시가 없습니다.",
                **corrected,
            }

        disclosure_items = [
            {
                "company_name": company.name,
                "title": row.title,
                "disclosure_type": row.disclosure_type,
                "disclosed_at": row.disclosed_at.isoformat() if row.disclosed_at else None,
                "source_url": row.source_url,
            }
            for row in rows
        ]

        return {
            "company_name": company.name,
            "limit": limit,
            "found": True,
            "disclosures": disclosure_items,
            **corrected,
        }
    finally:
        db.close()
