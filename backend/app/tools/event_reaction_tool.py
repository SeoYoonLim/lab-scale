"""뉴스·공시 이후 주가 반응 tool. 계산은 GET /api/stocks/{ticker}/event-reactions와 같은 함수
(app.event_reaction_service.build_event_reactions -> app.event_reaction)를 직접 호출한다(HTTP 호출·계산 중복 없음).

이 tool은 LLM에 tool 목록으로 노출하지 않는다. app/routing.py의 needs_event_reaction_tool 게이트를 통과한 질문에서만
코드가 실행한다(app/agent.py). 결과는 JSON으로 바로 넘길 수 있게 날짜를 문자열로 바꿔 돌려준다.
"""

import json

from app.db.session import SessionLocal
from app.event_reaction import DISCLAIMER
from app.event_reaction_service import build_event_reactions
from app.korean import josa
from app.tools.company_resolver import not_found_response, resolve_company, to_int

# 질문에 나오면 그 낱말이 제목에 든 사건만 고르는 키워드(이 목록 밖의 낱말로는 거르지 않는다).
TITLE_KEYWORDS = ("실적", "영업", "유상증자", "무상증자", "합병", "분할", "배당", "자기주식", "공급계약", "소송")
# 조회 기간(일). API 최대값과 같다 - 키워드로 거를 때 오래된 사건까지 볼 수 있게.
TOOL_SINCE_DAYS = 120
MAX_LIMIT = 10
_EVENT_TYPES = ("disclosure", "news", "all")
_TYPE_KO = {"disclosure": "공시", "news": "뉴스", "all": "공시·뉴스"}


def _jsonable(obj):
    return json.loads(json.dumps(obj, ensure_ascii=False, default=str))


def event_reaction_tool(company_name: str, event_type: str = "all", limit: int = 5, keyword: str | None = None) -> dict:
    """종목의 최근 공시·뉴스 이후 주가 반응(1/5/20거래일 수익률, 지수 대비 초과수익률, 거래량비)을 조회한다."""
    limit = to_int(limit, default=5, lo=1, hi=MAX_LIMIT)
    event_type = event_type if event_type in _EVENT_TYPES else "all"
    keyword = keyword if keyword in TITLE_KEYWORDS else None
    base = {"company_name": company_name, "event_type": event_type, "limit": limit, "keyword": keyword}
    db = SessionLocal()
    try:
        res = resolve_company(db, company_name)
        if res.company is None:
            return not_found_response(base, res)
        company = res.company
        data = build_event_reactions(
            db, company, event_type, limit, TOOL_SINCE_DAYS, title_keyword=keyword
        )
    finally:
        db.close()

    out = _jsonable(
        {
            **base,
            "company_name": company.name,
            "ticker": company.ticker,
            "asset_type": company.asset_type,
            "market_index": data["market_index"],
            "price_as_of": data["price_as_of"],
            "since_days": TOOL_SINCE_DAYS,
            "events": data["events"],
            "summary": data["summary"],
            "disclaimer": DISCLAIMER,
        }
    )
    if res.corrected_from:
        out["corrected_from"] = res.corrected_from
    if not out["events"]:
        what = _TYPE_KO["news" if company.asset_type == "etf" and event_type != "disclosure" else event_type]
        if company.asset_type == "etf" and event_type == "disclosure":
            reason = f"{josa(company.name, '은/는')} ETF라서 공시를 수집하지 않습니다. 대상 사건이 없습니다."
        else:
            cond = f" 제목에 '{keyword}'이(가) 들어간" if keyword else ""
            reason = f"최근 {TOOL_SINCE_DAYS}일 안에{cond} {company.name}의 {what} 사건이 없습니다. 대상 사건이 없습니다."
        out.update(found=False, message=reason)
    else:
        out["found"] = True
    return out
