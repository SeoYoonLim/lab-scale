from typing import Literal

from fastapi import APIRouter, HTTPException, Path, Query
from pydantic import BaseModel, Field, field_validator

from app.agent import ask_question
from app.reports import get_report, list_reports

router = APIRouter(prefix="/api", tags=["research"])

BIGINT_MAX = 9223372036854775807
MAX_QUESTION_LEN = 1000


class ResearchRequest(BaseModel):
    question: str = Field(max_length=MAX_QUESTION_LEN)

    @field_validator("question")
    @classmethod
    def not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("질문이 비어 있습니다.")
        return v


class Source(BaseModel):
    tool: str
    type: Literal["news", "disclosure"]
    title: str
    company_names: list[str]
    company_filter: str | None = None
    url: str | None = None


class ResearchResponse(BaseModel):
    answer: str
    used_tools: list[str]
    sources: list[Source] = []
    # 저장에 실패하면 None. 답변은 저장 성공 여부와 무관하게 정상 반환된다.
    report_id: int | None = None


class ReportDetail(BaseModel):
    report_id: int
    question: str
    answer: str
    summary: str | None = None
    # 질문에서 다룬 종목이 정확히 1개일 때만 채워진다(여러 개거나 없으면 None)
    company_name: str | None = None
    created_at: str
    used_tools: list[str]
    sources: list[Source]


class ReportListItem(BaseModel):
    report_id: int
    question: str
    summary: str | None = None
    company_name: str | None = None
    created_at: str
    used_tools: list[str]


class ReportList(BaseModel):
    total: int
    items: list[ReportListItem]


@router.post("/research", response_model=ResearchResponse)
def research(request: ResearchRequest) -> ResearchResponse:
    result = ask_question(request.question)
    return ResearchResponse(**result)


@router.get("/research", response_model=ReportList)
def list_research(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> ReportList:
    """최근 리포트 목록(최신순). total은 전체 리포트 수(페이지네이션용)."""
    total, items = list_reports(limit=limit, offset=offset)
    return ReportList(total=total, items=[ReportListItem(**r) for r in items])


@router.get("/research/{report_id}", response_model=ReportDetail)
def get_research(report_id: int = Path(ge=1, le=BIGINT_MAX)) -> ReportDetail:
    """저장된 리포트 한 건(질문/답변/sources)."""
    report = get_report(report_id)
    if report is None:
        raise HTTPException(status_code=404, detail=f"report_id={report_id} 리포트를 찾을 수 없습니다.")
    return ReportDetail(**report)
