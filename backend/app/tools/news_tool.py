from app.db.session import SessionLocal
from app.etf import mentions_etf_name
from app.korean import josa
from app.models import Company, News
from app.tools.company_resolver import not_found_response, resolve_company, to_int


# ETF 뉴스는 이름이 직접 나오는 기사만 남기므로 limit의 몇 배를 읽어서 거른다.
ETF_NEWS_OVERFETCH = 4


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

        is_etf = company.asset_type == "etf"
        rows = (
            db.query(News)
            .filter(News.company_id == company.id)
            .order_by(News.published_at.desc())
            # ETF는 이름이 나오지 않는 기사를 아래에서 걸러내므로 여유 있게 읽는다
            .limit(limit * ETF_NEWS_OVERFETCH if is_etf else limit)
            .all()
        )

        excluded = 0
        if is_etf:
            # ETF 이름으로 검색한 기사에는 ETF 시장 전반이나 같은 계열의 다른 상품 기사가 섞여 있다. 제목·본문에 이 ETF의
            # 이름이 직접 나오는 기사만 근거로 쓴다(더 긴 다른 ETF 이름의 일부로만 나오는 경우는 제외: "KODEX 200TR").
            others = [n for (n,) in db.query(Company.name).filter(Company.asset_type == "etf", Company.id != company.id)]
            related = [r for r in rows if mentions_etf_name(f"{r.title} {r.content or ''}", company.name, others)]
            excluded = len(rows) - len(related)
            rows = related[:limit]
            if not rows:
                return {
                    "company_name": company.name,
                    "limit": limit,
                    "found": False,
                    "asset_type": "etf",
                    "message": f"{josa(company.name, '이/가')} 직접 언급된 저장 뉴스가 없습니다.",
                    **corrected,
                }

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

        out = {
            "company_name": company.name,
            "limit": limit,
            "found": True,
            "news": news_items,
            **corrected,
        }
        if is_etf:
            out.update(asset_type="etf", excluded_unrelated=excluded)
        return out
    finally:
        db.close()
