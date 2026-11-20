import ollama

_LIBRARY_EMBED = ollama.embed  # 테스트가 ollama.embed를 바꿔 두면 그 함수를 쓴다
# 질문 임베딩(사용자 요청 경로) 상한. 넘기면 예외 -> agent._run_tool이 found=False 결과로 바꾼다(재시도 없음).
EMBED_TIMEOUT_S = 10
from sqlalchemy import Text, cast, func

from app.db.session import SessionLocal
from app.display_dates import kst_iso
from app.embeddings import EMBEDDING_MODEL
from app.llm_settings import embed_options
from app.models import Company, Disclosure, News
from app.news_relevance import is_duplicate_title, subject_match
from app.tools.company_resolver import not_found_response, resolve_company, to_int


def _embed_query(query: str) -> list[float]:
    if ollama.embed is _LIBRARY_EMBED:
        response = ollama.Client(timeout=EMBED_TIMEOUT_S).embed(model=EMBEDDING_MODEL, input=[query], **embed_options())
    else:
        response = ollama.embed(model=EMBEDDING_MODEL, input=[query], **embed_options())
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
    if model is News:
        base = base.filter(News.is_relevant.isnot(False))
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


CANDIDATE_POOL_MIN = 30  # 68차: 종목 지정 검색의 뉴스 후보 수 하한(근거 판정 전)
CANDIDATE_POOL_PER_RESULT = 6


def _embedded_count(db, company) -> int:
    """임베딩이 있는 뉴스(관련성 False 제외)·공시 수. 결과 0건이 데이터 부재인지 필터 탓인지 가른다."""
    total = 0
    for model in (News, Disclosure):
        q = db.query(func.count(model.id)).filter(model.embedding.isnot(None))
        if model is News:
            q = q.filter(News.is_relevant.isnot(False))
        if company is not None:
            q = q.filter(model.company_id == company.id)
        total += q.scalar() or 0
    return total


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


def select_news(company_name: str, asset_type: str | None, items: list[tuple[dict, str | None]], other_etf_names=()) -> tuple[list[dict], dict]:
    """68차: 종목 지정 검색의 뉴스 근거 판정(유사도 순서 유지). items = [(결과 항목, 본문 전체)].
    제목 또는 본문 첫머리에 그 종목 이름이 있어야 하고(app.news_relevance.subject_match), 거의 같은 제목은 하나만 남긴다.
    (남긴 항목, 제외 사유별 건수)."""
    kept: list[dict] = []
    excluded = {"subject_not_in_title_or_lead": 0, "duplicate_title": 0}
    for item, content in items:
        match = subject_match(company_name, asset_type, item.get("title"), content, other_etf_names=other_etf_names)
        if match is None:
            excluded["subject_not_in_title_or_lead"] += 1
            continue
        if any(is_duplicate_title(item.get("title"), k.get("title")) for k in kept):
            excluded["duplicate_title"] += 1
            continue
        kept.append({**item, "match": match})
    return kept, {k: v for k, v in excluded.items() if v}


def empty_response(query: str, company_name: str | None, limit: int, embedded: int, search: dict, corrected: dict) -> dict:
    """결과 0건 응답. 임베딩이 하나도 없을 때(no_embeddings)와 후보는 있었지만 근거 판정에서 모두 빠졌을 때(no_relevant_match)를 가른다.
    전에는 둘 다 "임베딩된 데이터가 없다"고 답했다(68차 KODEX 200)."""
    base = {"query": query, "company_name": company_name, "limit": limit, "found": False, "search": search, **corrected}
    if embedded == 0:
        return {**base, "reason": "no_embeddings", "message": "임베딩된 news/disclosure 데이터가 없어 검색할 수 없습니다."}
    n = sum((search.get("candidates") or {}).values())
    return {**base, "reason": "no_relevant_match",
            "message": (f"검색 후보 {n}건이 있었지만 {company_name or '질문 대상'}을(를) 제목이나 본문 첫머리에서 직접 다룬 문서가 없어 "
                        "결과에서 뺐습니다. 임베딩 데이터가 없는 것이 아닙니다.")}


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

        # 68차: 종목을 지정하면 뉴스 후보를 넉넉히 뽑은 뒤 근거 판정(제목 또는 본문 첫머리에 그 종목 이름)으로 거른다.
        # 전에는 limit개만 뽑고 제목 필터를 걸어, 관련 기사가 6위 아래에 있으면 0건이 되고 "임베딩 데이터가 없다"고 답했다(KODEX 200).
        pool = max(limit * CANDIDATE_POOL_PER_RESULT, CANDIDATE_POOL_MIN) if company is not None else limit
        news_rows = _top_unique(db, News, News.url, query_vector, company, pool)
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

        others = ([n for (n,) in db.query(Company.name).filter(Company.asset_type == "etf", Company.id != company.id)]
                  if company is not None and company.asset_type == "etf" else [])
        news_items = [({
            "type": "news",
            "company_names": names_for(news_owners, row.url, row),
            "title": row.title,
            "content_snippet": (row.content or "")[:200],
            "url": row.url,
            "published_at": kst_iso(row.published_at),  # 39차: KST 문자열
            "score": round(1 - distance, 4),
        }, row.content) for row, distance in news_rows]
        if company is not None:
            news_results, excluded = select_news(company.name, company.asset_type, news_items, others)
        else:
            news_results, excluded = [it for it, _ in news_items], {}
        results = news_results[:limit]
        for row, distance in disclosure_rows:
            results.append(
                {
                    "type": "disclosure",
                    "company_names": names_for(disc_owners, row.source_url, row),
                    "title": row.title,
                    "content_snippet": (row.content or "")[:200],
                    "content_available": bool((row.content or "").strip()),  # 68차: 본문 없는 공시(원문 없음)는 제목만 근거다
                    "source_url": row.source_url,
                    "disclosed_at": kst_iso(row.disclosed_at),  # 39차: KST 문자열
                    "score": round(1 - distance, 4),
                }
            )

        # news/disclosure를 각각 top-k로 뽑은 뒤 합쳤으니, 최종 유사도 기준으로 다시 정렬해서 자른다.
        results.sort(key=lambda r: r["score"], reverse=True)
        results = results[:limit]
        search = {"candidates": {"news": len(news_rows), "disclosure": len(disclosure_rows)},
                  "kept_news": len(news_results), "excluded": excluded}

        response_company = company.name if company is not None else None
        if not results:
            return empty_response(query, response_company, limit, _embedded_count(db, company), search, corrected)

        return {
            "query": query,
            "company_name": response_company,
            "limit": limit,
            "found": True,
            "results": results,
            "search": search,
            **corrected,
        }
    finally:
        db.close()
