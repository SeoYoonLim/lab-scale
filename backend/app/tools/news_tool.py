from app.db.session import SessionLocal
from app.korean import josa
from app.models import Company, News
from app.news_relevance import evidence_news
from app.tools.company_resolver import not_found_response, resolve_company, to_int


# 제목에 이름이 직접 나오는 기사만 남기므로 limit의 몇 배를 읽어서 거른다(2026-11-03 4 -> 8: SK하이닉스는 최근 20건 중 제목 일치가 0건이었다).
ETF_NEWS_OVERFETCH = 8


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
            .filter(News.company_id == company.id, News.is_relevant.isnot(False))
            .order_by(News.published_at.desc())
            .limit(limit * ETF_NEWS_OVERFETCH)
            .all()
        )

        # 근거 후처리(app.news_relevance.evidence_news, 전망 경로·RAG와 같은 함수): 제목에 이 종목 이름이 있는 기사만, 비슷한 제목은 하나만.
        # ETF는 더 긴 다른 ETF 이름의 일부로만 나오는 경우를 뺀다("KODEX 200TR"). 2026-11-03 전에는 주식은 제목 일치를 앞에 두고
        # 나머지도 보여 줬고, ETF는 본문 언급도 인정했다.
        others = ([n for (n,) in db.query(Company.name).filter(Company.asset_type == "etf", Company.id != company.id)]
                  if is_etf else [])
        candidates = [{"row": r, "title": r.title} for r in rows]
        kept = evidence_news(company.name, company.asset_type, candidates, other_etf_names=others)
        excluded = len(rows) - len(kept)
        rows = [k["row"] for k in kept][:limit]

        if not rows:
            hidden = db.query(News.id).filter(News.company_id == company.id).first() is not None
            out = {
                "company_name": company.name,
                "limit": limit,
                "found": False,
                "message": f"{josa(company.name, '이/가')} 제목에 나오는 관련 뉴스를 찾지 못했습니다." if hidden
                else f"'{company.name}'는 등록된 종목이지만 아직 수집된 뉴스가 없습니다.",
                "excluded_unrelated": excluded,
                **corrected,
            }
            if is_etf:
                out["asset_type"] = "etf"
            return out

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
            "excluded_unrelated": excluded,
            **corrected,
        }
        if is_etf:
            out["asset_type"] = "etf"
        return out
    finally:
        db.close()
