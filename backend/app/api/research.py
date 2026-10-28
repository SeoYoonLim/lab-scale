from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Response
from pydantic import BaseModel, Field, field_validator

from app import rate_limit
from app.agent import ask_question
from app.api.deps import get_current_user
from app.auth import CurrentUser
from app.disclaimer import DISCLAIMER
from app.pagination import MAX_OFFSET, page_meta
from app.reports import delete_report, get_report, list_reports

router = APIRouter(prefix="/api", tags=["research"])

BIGINT_MAX = 9223372036854775807
MAX_QUESTION_LEN = 1000
MAX_SEARCH_LEN = 50


class ResearchRequest(BaseModel):
    question: str = Field(max_length=MAX_QUESTION_LEN)
    # 후속 질문일 때 이어받을 바로 직전 보고서. 그 보고서의 질문/답변이 대화 맥락으로 쓰인다(체이닝은 1개까지).
    previous_report_id: int | None = Field(default=None, ge=1, le=BIGINT_MAX)

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
    # 요청에서 이어받은 직전 보고서. 후속 질문이 아니면 None.
    previous_report_id: int | None = None
    disclaimer: str = DISCLAIMER


class ReportDetail(BaseModel):
    report_id: int
    previous_report_id: int | None = None
    question: str
    answer: str
    summary: str | None = None
    # 질문에서 다룬 종목이 정확히 1개일 때만 채워진다(여러 개거나 없으면 None)
    company_name: str | None = None
    created_at: str
    used_tools: list[str]
    sources: list[Source]
    disclaimer: str = DISCLAIMER


class ReportListItem(BaseModel):
    report_id: int
    previous_report_id: int | None = None
    question: str
    summary: str | None = None
    company_name: str | None = None
    created_at: str
    used_tools: list[str]


class ReportList(BaseModel):
    # total은 q를 적용한 건수다(q가 없으면 내 전체 리포트 수). has_next/total_pages는 total, limit, offset에서 계산한다.
    total: int
    has_next: bool
    total_pages: int
    items: list[ReportListItem]


def _report_not_found(report_id: int) -> HTTPException:
    # 남의 리포트도 "없음"과 똑같이 404로 응답한다(403이면 그 id가 존재한다는 사실이 드러난다).
    return HTTPException(status_code=404, detail=f"report_id={report_id} 리포트를 찾을 수 없습니다.")


@router.post("/research", response_model=ResearchResponse)
def research(request: ResearchRequest, user: CurrentUser = Depends(get_current_user)) -> ResearchResponse:
    # 인증(401) 다음에 사용자당 분당 횟수와 동시 1건을 본다(429). LLM이 Ollama를 오래 점유하기 때문이다(app/rate_limit).
    rate_limit.check(rate_limit.research, user.owner_key, "RATE_LIMIT_RESEARCH_PER_MIN")
    with rate_limit.research_slot(user.owner_key):
        if request.previous_report_id is None:
            result = ask_question(request.question, user_id=user.id)
        else:
            # 이어 쓰기도 본인 리포트만. 남의 리포트 id면 없는 것과 같은 404다.
            previous = get_report(request.previous_report_id, user_id=user.id)
            if previous is None:
                raise _report_not_found(request.previous_report_id)
            result = ask_question(request.question, previous=previous, user_id=user.id)
    return ResearchResponse(**result)


@router.get("/research", response_model=ReportList)
def list_research(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0, le=MAX_OFFSET),
    sort: Literal["created_at", "id"] = Query("created_at", description="정렬 기준"),
    order: Literal["asc", "desc"] = Query("desc", description="정렬 방향. 기본은 최신순(desc)"),
    q: str | None = Query(
        None,
        max_length=MAX_SEARCH_LEN,
        description="질문·요약·종목명·종목코드 부분 일치 검색(대소문자 무시, %/_는 리터럴). 공백만이면 무시",
    ),
    user: CurrentUser = Depends(get_current_user),
) -> ReportList:
    """내 리포트 목록(기본 최신순). total은 q를 적용한 건수(페이지네이션용), has_next/total_pages는 그 total 기준이다."""
    total, items = list_reports(user_id=user.id, limit=limit, offset=offset, sort=sort, order=order, q=q)
    return ReportList(total=total, **page_meta(total, limit, offset), items=[ReportListItem(**r) for r in items])


@router.get("/research/{report_id}", response_model=ReportDetail)
def get_research(
    report_id: int = Path(ge=1, le=BIGINT_MAX), user: CurrentUser = Depends(get_current_user)
) -> ReportDetail:
    """내 리포트 한 건(질문/답변/sources)."""
    report = get_report(report_id, user_id=user.id)
    if report is None:
        raise _report_not_found(report_id)
    return ReportDetail(**report)


@router.delete("/research/{report_id}", status_code=204)
def delete_research(
    report_id: int = Path(ge=1, le=BIGINT_MAX), user: CurrentUser = Depends(get_current_user)
) -> Response:
    """내 리포트 한 건과 딸린 tool 호출 이력을 삭제한다."""
    if not delete_report(report_id, user_id=user.id):
        raise _report_not_found(report_id)
    return Response(status_code=204)
