from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Response
from pydantic import BaseModel, Field, field_validator

from app import rate_limit
from app.agent import ask_question
from app.api.deps import get_current_user
from app.api.history_params import history_window, with_dates
from app.auth import CurrentUser
from app.disclaimer import DISCLAIMER
from app.history_dates import DEFAULT_TIMEZONE, MAX_TIMEZONE_LEN
from app.i18n import DEFAULT_LOCALE, MAX_LOCALE_LEN, disclaimer, is_default, resolve_locale
from app.pagination import MAX_OFFSET, page_meta
from app.reports import delete_report, get_report, list_reports

AnalysisType = Literal["outlook", "event", "briefing", "discovery", "etf", "disclosure", "news", "price", "market", "general"]
DateGroup = Literal["today", "yesterday", "last_7_days", "last_30_days", "older"]
# 2026-11-09(38차): 새 답변에서 나올 수 있는 번역 상태(app.answer_i18n.localize_answer가 돌려주는 값 전부).
# fallback_original은 2026-11-07 이전에 저장된 기록에만 있어 POST 응답에는 나오지 않는다(ReportRecordDetail은 str 그대로).
TranslationStatus = Literal["translated", "localized", "fallback_summary"]

router = APIRouter(prefix="/api", tags=["research"])

BIGINT_MAX = 9223372036854775807
MAX_QUESTION_LEN = 1000
MAX_SEARCH_LEN = 50


class ResearchRequest(BaseModel):
    question: str = Field(max_length=MAX_QUESTION_LEN)
    # 후속 질문일 때 이어받을 바로 직전 보고서. 그 보고서의 질문/답변이 대화 맥락으로 쓰인다(체이닝은 1개까지).
    previous_report_id: int | None = Field(default=None, ge=1, le=BIGINT_MAX)
    # 답변 언어(2026-11-05): ko-KR, en-US, ja-JP, zh-CN. 없으면 계정 설정(preferred_locale), 지원하지 않는 값이면 ko-KR.
    locale: str | None = Field(default=None, max_length=MAX_LOCALE_LEN)

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
    # 2026-11-09(38차, 하위 호환 추가): 답변이 실제로 쓰인 언어. 요청 locale이 아니라 결정된 값이다
    # (지원하지 않는 locale을 보냈으면 ko-KR). GET /api/research/{id}?view=record의 locale과 같은 값이 저장된다.
    locale: str = Field(DEFAULT_LOCALE, description="답변이 실제로 쓰인 언어(요청 locale > 계정 설정 > ko-KR, 지원하지 않는 값은 ko-KR)")
    # ko-KR 답변은 번역하지 않으므로 null. 그 밖의 언어는 기록 상세(view=record)의 translation_status와 같은 값이다.
    translation_status: TranslationStatus | None = Field(
        None,
        description="ko-KR이면 null(번역 안 함). translated=보호 번역 성공, localized=코드가 그 언어로 직접 쓴 답, "
                    "fallback_summary=번역을 검증하지 못해 해설 대신 그 언어 안내 + 데이터 요약(한국어 원문은 view=record의 original_answer)",
    )


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
    # total은 q·날짜·유형 조건을 적용한 건수다(조건이 없으면 내 전체 리포트 수). has_next/total_pages는 total, limit, offset에서 계산한다.
    total: int
    has_next: bool
    total_pages: int
    items: list[ReportListItem]


# ---------- view=record (2026-11-05): 기록 관리 화면용. 기본 응답(view 없음)은 위 모델 그대로다 ----------


class RecordFields(BaseModel):
    """저장된 리포트와 tool 이력에서 읽기만 해서 만드는 기록 관리 필드."""

    # 질문을 공백 정리해 앞 60자(제목 컬럼이 없어서 질문에서 만든다)
    title: str
    # 리포트 종목 + tool 결과에 나온 종목(순서 유지, 중복 없음). company_name과 달리 여러 종목이면 모두 담긴다
    company_names: list[str]
    tickers: list[str]
    # tool 이력으로 정한 분석 유형. analysis_type은 analysis_types의 첫 값
    analysis_type: AnalysisType
    analysis_types: list[AnalysisType]
    # 뉴스·공시 출처 수(상세 sources와 같은 규칙으로 중복 제거)
    source_count: int
    # 요청 시간대(기본 Asia/Seoul) 기준 표시용 날짜. created_at(UTC)은 그대로다
    local_date: str
    local_time: str
    date_group: DateGroup


class DataBasis(BaseModel):
    """당시 tool 결과에 남은 기준일. 키는 결과에 있던 것만 채워진다(나머지는 null)."""

    tool: str
    company_name: str | None = None
    ticker: str | None = None
    period_start_date: str | None = None
    period_end_date: str | None = None
    base_date: str | None = None
    latest_date: str | None = None
    data_date: str | None = None
    price_as_of: str | None = None
    as_of: str | None = None


class ReportRecordItem(RecordFields, ReportListItem):
    # 기록을 만든 언어(기존 기록은 ko-KR)
    locale: str


class ReportRecordList(BaseModel):
    total: int
    has_next: bool
    total_pages: int
    # local_date·date_group을 계산한 시간대
    timezone: str
    items: list[ReportRecordItem]


class ReportRecordDetail(RecordFields, ReportDetail):
    data_basis: list[DataBasis]
    timezone: str
    locale: str
    # ko-KR이 아닌 기록만: translated(번역) | localized(그 언어 템플릿) | fallback_summary(번역 검증 실패, answer는 그 언어 안내 +
    # 데이터 요약, 2026-11-07부터) | fallback_original(2026-11-07 이전 기록의 번역 검증 실패, answer는 안내 + 한국어 원문)
    translation_status: str | None = Field(
        None,
        description="ko-KR 기록이면 null. translated | localized | fallback_summary | fallback_original(2026-11-07 이전 기록만)",
    )
    # ko-KR이 아닌 기록만: 번역 전에 검증을 거친 한국어 답변
    original_answer: str | None = None


View = Literal["default", "record"]


def view_query():
    return Query("default", description="record면 기록 관리 필드(제목·종목 목록·분석 유형·출처 수·날짜 그룹)를 붙인다")


def timezone_query():
    return Query(
        None,
        max_length=MAX_TIMEZONE_LEN,
        description="날짜 범위·날짜 그룹의 시간대(IANA 이름, 예: Asia/Seoul). 없으면 Asia/Seoul. 모르는 이름은 422",
    )


def _report_not_found(report_id: int) -> HTTPException:
    # 남의 리포트도 "없음"과 똑같이 404로 응답한다(403이면 그 id가 존재한다는 사실이 드러난다).
    return HTTPException(status_code=404, detail=f"report_id={report_id} 리포트를 찾을 수 없습니다.")


@router.post("/research", response_model=ResearchResponse)
def research(request: ResearchRequest, user: CurrentUser = Depends(get_current_user)) -> ResearchResponse:
    # 인증(401) 다음에 사용자당 분당 횟수와 동시 1건을 본다(429). LLM이 Ollama를 오래 점유하기 때문이다(app/rate_limit).
    rate_limit.check(rate_limit.research, user.owner_key, "RATE_LIMIT_RESEARCH_PER_MIN")
    # 답변 언어: 요청 locale > 계정 설정 > ko-KR. ko-KR이면 예전과 같은 호출이다(locale 인자를 넘기지 않는다).
    locale = resolve_locale(request.locale, user.preferred_locale)
    localized = {} if is_default(locale) else {"locale": locale}
    with rate_limit.research_slot(user.owner_key):
        if request.previous_report_id is None:
            result = ask_question(request.question, user_id=user.id, **localized)
        else:
            # 이어 쓰기도 본인 리포트만. 남의 리포트 id면 없는 것과 같은 404다.
            previous = get_report(request.previous_report_id, user_id=user.id)
            if previous is None:
                raise _report_not_found(request.previous_report_id)
            # 번역된 기록이면 후속 질문의 맥락은 번역 전 한국어 답변이다(전망 표식 등 한국어 규칙이 그 답을 읽는다)
            if previous.get("original_answer"):
                previous = {**previous, "answer": previous["original_answer"]}
            result = ask_question(request.question, previous=previous, user_id=user.id, **localized)
    # 38차: 결정된 언어와 번역 상태를 같이 준다. 번역 상태는 ask_question의 내부 meta에서만 꺼낸다(ko-KR이면 meta에 없다 → null)
    status = (result.get("meta") or {}).get("translation_status") if localized else None
    if localized:
        return ResearchResponse(**{**result, "disclaimer": disclaimer(locale)}, locale=locale, translation_status=status)
    return ResearchResponse(**result, locale=locale)


@router.get("/research", response_model=ReportList | ReportRecordList)
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
    date_from: date | None = Query(None, description="이 날짜(timezone 기준)부터. YYYY-MM-DD"),
    date_to: date | None = Query(None, description="이 날짜(timezone 기준)까지, 그날 끝까지 포함. YYYY-MM-DD"),
    timezone: str | None = timezone_query(),
    analysis_type: AnalysisType | None = Query(None, description="분석 유형 필터(analysis_types에 그 값이 있는 리포트)"),
    view: View = view_query(),
    user: CurrentUser = Depends(get_current_user),
) -> ReportList | ReportRecordList:
    """내 리포트 목록(기본 최신순). total은 조건을 적용한 건수(페이지네이션용), has_next/total_pages는 그 total 기준이다.
    날짜·유형 조건(2026-11-05)은 준 것만 넘긴다: 아무것도 없으면 예전과 같은 호출이다."""
    tz, start, end = history_window(date_from, date_to, timezone or user.preferred_timezone)
    filters = {k: v for k, v in {"start": start, "end": end, "analysis_type": analysis_type}.items() if v is not None}
    total, items = list_reports(user_id=user.id, limit=limit, offset=offset, sort=sort, order=order, q=q, **filters)
    meta = page_meta(total, limit, offset)
    if view == "record":
        return ReportRecordList(total=total, **meta, timezone=tz.key, items=[ReportRecordItem(**with_dates(r, tz)) for r in items])
    return ReportList(total=total, **meta, items=[ReportListItem(**r) for r in items])


@router.get("/research/{report_id}", response_model=ReportDetail | ReportRecordDetail)
def get_research(
    report_id: int = Path(ge=1, le=BIGINT_MAX),
    timezone: str | None = timezone_query(),
    view: View = view_query(),
    user: CurrentUser = Depends(get_current_user),
) -> ReportDetail | ReportRecordDetail:
    """내 리포트 한 건(질문/답변/sources). 저장된 답변·tool 결과 그대로이고 다시 계산하지 않는다."""
    tz, _, _ = history_window(None, None, timezone or user.preferred_timezone)
    report = get_report(report_id, user_id=user.id)
    if report is None:
        raise _report_not_found(report_id)
    if view == "record":
        return ReportRecordDetail(**with_dates(report, tz), timezone=tz.key)
    return ReportDetail(**report)


@router.delete("/research/{report_id}", status_code=204)
def delete_research(
    report_id: int = Path(ge=1, le=BIGINT_MAX), user: CurrentUser = Depends(get_current_user)
) -> Response:
    """내 리포트 한 건과 딸린 tool 호출 이력을 삭제한다."""
    if not delete_report(report_id, user_id=user.id):
        raise _report_not_found(report_id)
    return Response(status_code=204)
