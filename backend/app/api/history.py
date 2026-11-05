"""GET /api/history/search: 내 리서치·진단 기록 통합 검색(인증 필요). 2026-11-05.

상세는 각자의 기존 API를 쓴다: 리서치는 GET /api/research/{report_id}, 진단은 GET /api/portfolio/diagnosis/{diagnosis_id}
(항목의 detail_path). 검색어 규칙은 app/history_terms, 날짜·시간대 규칙은 app/history_dates.
"""

from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app import history_search
from app.api.deps import get_current_user
from app.api.history_params import history_window, with_dates
from app.api.portfolio import DiagnosisFlagBrief, TopHolding
from app.api.research import AnalysisType, DateGroup, timezone_query
from app.auth import CurrentUser
from app.history_terms import MAX_KEYWORD_LEN, split_terms
from app.pagination import page_meta

router = APIRouter(prefix="/api/history", tags=["history"])

MAX_PAGE = 10_000
MAX_SIZE = 100


class ResearchPart(BaseModel):
    report_id: int
    previous_report_id: int | None = None
    question: str
    analysis_type: AnalysisType
    analysis_types: list[AnalysisType]
    source_count: int
    used_tools: list[str]


class DiagnosisPart(BaseModel):
    diagnosis_id: int
    source: Literal["llm", "rule_based"]
    total_asset: float
    holdings_count: int
    cash_weight_pct: float | None = None
    top_holdings: list[TopHolding]
    risk_summary: str | None = None
    flags: list[DiagnosisFlagBrief]
    flag_codes: list[str]
    generated_at: str | None = None


class HistoryItem(BaseModel):
    # 기록 종류. 상세 조회는 detail_path(기존 API)로 한다
    kind: Literal["research", "diagnosis"]
    id: int
    created_at: str
    local_date: str
    local_time: str
    date_group: DateGroup
    title: str
    summary: str | None = None
    company_names: list[str]
    tickers: list[str]
    detail_path: str
    research: ResearchPart | None = None
    diagnosis: DiagnosisPart | None = None


class HistoryCounts(BaseModel):
    all: int
    research: int
    diagnosis: int


class HistorySearchResponse(BaseModel):
    type: Literal["all", "research", "diagnosis"]
    keyword: str | None = None
    # 실제로 검색에 쓴 낱말(공백으로 나눈 것, 최대 5개)
    terms: list[str]
    date_from: str | None = None
    date_to: str | None = None
    timezone: str
    sort: str
    page: int
    size: int
    # type 탭의 건수. counts는 탭마다의 건수(type과 무관하게 같이 센다)
    total: int
    total_pages: int
    has_next: bool
    counts: HistoryCounts
    items: list[HistoryItem]


def _research_item(r: dict) -> dict:
    return {
        "kind": "research", "id": r["report_id"], "created_at": r["created_at"], "created_at_dt": r["created_at_dt"],
        "title": r["title"], "summary": r["summary"], "company_names": r["company_names"], "tickers": r["tickers"],
        "detail_path": f"/api/research/{r['report_id']}",
        "research": {**r},
    }


def _diagnosis_item(d: dict) -> dict:
    top = d["top_holdings"]
    return {
        "kind": "diagnosis", "id": d["id"], "created_at": d["created_at"], "created_at_dt": d["created_at_dt"],
        "title": d["title"], "summary": d["summary_preview"],
        "company_names": [h["company_name"] for h in top if h.get("company_name")],
        "tickers": [h["ticker"] for h in top if h.get("ticker")],
        "detail_path": f"/api/portfolio/diagnosis/{d['id']}",
        "diagnosis": {**d, "diagnosis_id": d["id"]},
    }


@router.get("/search", response_model=HistorySearchResponse)
def search_history(
    type: Literal["all", "research", "diagnosis"] = Query("all", description="기록 종류 탭"),
    keyword: str | None = Query(
        None, max_length=MAX_KEYWORD_LEN,
        description="공백으로 나눈 낱말이 모두 들어 있는 기록(최대 5개 낱말, 대소문자 무시, %/_는 리터럴). 없으면 전체",
    ),
    date_from: date | None = Query(None, description="이 날짜(timezone 기준)부터. YYYY-MM-DD"),
    date_to: date | None = Query(None, description="이 날짜(timezone 기준)까지, 그날 끝까지 포함. YYYY-MM-DD"),
    timezone: str | None = timezone_query(),
    page: int = Query(1, ge=1, le=MAX_PAGE),
    size: int = Query(20, ge=1, le=MAX_SIZE),
    sort: Literal["created_at,desc", "created_at,asc"] = Query("created_at,desc", description="작성 시각 기준. 기본은 최신순"),
    user: CurrentUser = Depends(get_current_user),
) -> HistorySearchResponse:
    """내 리서치·진단 기록을 한 목록으로 검색한다(기본 최신순). 다른 사용자의 기록은 어떤 조건으로도 나오지 않는다."""
    tz, start, end = history_window(date_from, date_to, timezone)
    terms = split_terms(keyword)
    offset = (page - 1) * size
    result = history_search.search(
        user.id, kind=type, terms=terms, start=start, end=end, limit=size, offset=offset, order=sort.split(",")[1],
    )
    items = [with_dates(_research_item(i) if k == "research" else _diagnosis_item(i), tz) for k, i in result["items"]]
    total = result["total"]
    counts = result["counts"]
    return HistorySearchResponse(
        type=type, keyword=keyword, terms=terms,
        date_from=date_from.isoformat() if date_from else None, date_to=date_to.isoformat() if date_to else None,
        timezone=tz.key, sort=sort, page=page, size=size, total=total, **page_meta(total, size, offset),
        counts={"all": counts["research"] + counts["diagnosis"], **counts}, items=items,
    )
