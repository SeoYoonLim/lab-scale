import ollama
from sqlalchemy import Text, cast, func

from app.db.session import SessionLocal
from app.embeddings import EMBEDDING_MODEL
from app.models import Company, Disclosure, News
from app.tools.company_resolver import not_found_response, resolve_company, to_int


def _embed_query(query: str) -> list[float]:
    response = ollama.embed(model=EMBEDDING_MODEL, input=[query])
    return response["embeddings"][0]


def _top_unique(db, model, url_attr, query_vector, company, limit):
    """url(news) / source_url(disclosure) 기준으로 중복을 제거한 유사도 top-k를 뽑는다.

    같은 기사가 여러 종목에 각각 저장돼 있어도(종목별 dedupe) 한 건으로 취급한다. 중복이 top-k
    자리를 차지하지 않도록 자르기 전에 DB에서 DISTINCT ON으로 먼저 줄인다. url이 NULL인 행은
    서로 합쳐지지 않게 id를 키로 쓴다.
    """
    distance = model.embedding.cosine_distance(query_vector).label("distance")
    key = func.coalesce(url_attr, cast(model.id, Text))

    base = db.query(model.id.label("id"), distance).filter(model.embedding.isnot(None))
    if company is not None:
        base = base.filter(model.company_id == company.id)
    best_per_doc = base.distinct(key).order_by(key, distance).subquery()

    return (
        db.query(model, best_per_doc.c.distance)
        .join(best_per_doc, model.id == best_per_doc.c.id)
        .order_by(best_per_doc.c.distance)
        .limit(limit)
        .all()
    )


def _owner_names(db, model, url_attr, urls) -> dict[str, list[str]]:
    """문서(url)별로 그 문서가 저장된 종목명 목록을 조회한다."""
    urls = [u for u in urls if u]
    if not urls:
        return {}
    rows = (
        db.query(url_attr, Company.name)
        .join(Company, Company.id == model.company_id)
        .filter(url_attr.in_(urls))
        .order_by(Company.name)
        .all()
    )
    out: dict[str, list[str]] = {}
    for url, name in rows:
        if name not in out.setdefault(url, []):
            out[url].append(name)
    return out


def rag_search_tool(query: str, company_name: str | None = None, limit: int = 5) -> dict:
    """자연어 질문을 bge-m3로 임베딩해, news/disclosure 중 코사인 거리 기준으로
    의미적으로 가장 유사한 문서 top-k를 찾는다. 최신순이 아니라 유사도순이다.

    같은 기사/공시는 한 번만 나오고, company_names에 그 문서가 저장된 종목명 목록이 담긴다.
    (종목을 지정하면 그 종목만 담는다.)"""
    limit = to_int(limit, default=5, hi=20)
    db = SessionLocal()
    try:
        company = None
        corrected = {}
        if company_name is not None and str(company_name).strip():
            res = resolve_company(db, company_name)
            if res.company is None:
                return not_found_response({"query": query, "company_name": company_name, "limit": limit}, res)
            company = res.company
            if res.corrected_from:
                corrected = {"corrected_from": res.corrected_from}

        query_vector = _embed_query(query)

        news_rows = _top_unique(db, News, News.url, query_vector, company, limit)
        disclosure_rows = _top_unique(db, Disclosure, Disclosure.source_url, query_vector, company, limit)

        if company is None:
            news_owners = _owner_names(db, News, News.url, [r.url for r, _ in news_rows])
            disc_owners = _owner_names(db, Disclosure, Disclosure.source_url, [r.source_url for r, _ in disclosure_rows])
        else:
            news_owners = disc_owners = {}

        def names_for(owners, url, row):
            if company is not None:
                return [company.name]
            return owners.get(url) or ([row.company.name] if row.company else [])

        results = []
        for row, distance in news_rows:
            results.append(
                {
                    "type": "news",
                    "company_names": names_for(news_owners, row.url, row),
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
                    "company_names": names_for(disc_owners, row.source_url, row),
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

        response_company = company.name if company is not None else None
        if not results:
            return {
                "query": query,
                "company_name": response_company,
                "limit": limit,
                "found": False,
                "message": "임베딩된 news/disclosure 데이터가 없어 검색할 수 없습니다.",
                **corrected,
            }

        return {
            "query": query,
            "company_name": response_company,
            "limit": limit,
            "found": True,
            "results": results,
            **corrected,
        }
    finally:
        db.close()
