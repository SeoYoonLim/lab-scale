import ollama
from sqlalchemy import or_

from app.db.session import SessionLocal
from app.embeddings import EMBEDDING_MODEL
from app.models import Company, Disclosure, News


def _embed_query(query: str) -> list[float]:
    response = ollama.embed(model=EMBEDDING_MODEL, input=[query])
    return response["embeddings"][0]


def rag_search_tool(query: str, company_name: str | None = None, limit: int = 5) -> dict:
    """자연어 질문을 bge-m3로 임베딩해, news/disclosure 중 코사인 거리 기준으로
    의미적으로 가장 유사한 문서 top-k를 찾는다. 최신순이 아니라 유사도순이다."""
    db = SessionLocal()
    try:
        company = None
        if company_name:
            company = (
                db.query(Company)
                .filter(or_(Company.name == company_name, Company.ticker == company_name))
                .first()
            )
            if company is None:
                return {
                    "query": query,
                    "company_name": company_name,
                    "limit": limit,
                    "found": False,
                    "message": f"'{company_name}'는 company 테이블에 등록되지 않은 종목입니다. 데이터 없음.",
                }

        query_vector = _embed_query(query)

        news_query = db.query(
            News,
            News.embedding.cosine_distance(query_vector).label("distance"),
        ).filter(News.embedding.isnot(None))
        disclosure_query = db.query(
            Disclosure,
            Disclosure.embedding.cosine_distance(query_vector).label("distance"),
        ).filter(Disclosure.embedding.isnot(None))

        if company is not None:
            news_query = news_query.filter(News.company_id == company.id)
            disclosure_query = disclosure_query.filter(Disclosure.company_id == company.id)

        news_rows = news_query.order_by("distance").limit(limit).all()
        disclosure_rows = disclosure_query.order_by("distance").limit(limit).all()

        results = []
        for row, distance in news_rows:
            results.append(
                {
                    "type": "news",
                    "title": row.title,
                    "content_snippet": (row.content or "")[:200],
                    "url": row.url,
                    "published_at": row.published_at.isoformat() if row.published_at else None,
                    "score": round(1 - distance, 4),
                }
            )
        for row, distance in disclosure_rows:
            results.append(
                {
                    "type": "disclosure",
                    "title": row.title,
                    "content_snippet": (row.content or "")[:200],
                    "source_url": row.source_url,
                    "disclosed_at": row.disclosed_at.isoformat() if row.disclosed_at else None,
                    "score": round(1 - distance, 4),
                }
            )

        # news/disclosure를 각각 top-k로 뽑은 뒤 합쳤으니, 최종 유사도 기준으로 다시 정렬해서 자른다.
        results.sort(key=lambda r: r["score"], reverse=True)
        results = results[:limit]

        if not results:
            return {
                "query": query,
                "company_name": company_name,
                "limit": limit,
                "found": False,
                "message": "임베딩된 news/disclosure 데이터가 없어 검색할 수 없습니다.",
            }

        return {
            "query": query,
            "company_name": company_name,
            "limit": limit,
            "found": True,
            "results": results,
        }
    finally:
        db.close()
