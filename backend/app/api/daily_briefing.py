"""개인화 일일 투자 브리핑 API(2026-11-06). 모든 라우트는 로그인이 필요하고 본인 브리핑만 다룬다(남의 id는 404).

기존 GET /api/briefing(요청할 때마다 다시 계산하는 화면용 응답, 저장 안 함)은 그대로 둔다. 이 API는 그 계산을 재사용해 5개 영역의
브리핑을 만들고 날짜별로 저장한다(app/daily_briefing, app/daily_briefing_db).
"""

from datetime import date
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Response
from pydantic import BaseModel, Field

from app import daily_briefing_db as store
from app import rate_limit
from app.api.deps import get_current_user
from app.api.history_params import history_window
from app.api.research import DateGroup, timezone_query
from app.auth import CurrentUser
from app.db.session import SessionLocal
from app.i18n import MAX_LOCALE_LEN, resolve_locale
from app.pagination import page_meta

router = APIRouter(prefix="/api/briefings/daily", tags=["daily-briefing"])

BIGINT_MAX = 9223372036854775807
MAX_PAGE = 10_000
MAX_SIZE = 100
NOT_FOUND = "브리핑을 찾을 수 없습니다."
NOT_GENERATED = "오늘의 브리핑이 아직 없습니다. POST /api/briefings/daily/generate로 만들 수 있습니다."
TOO_MANY = f"오늘은 브리핑을 {store.MAX_VERSIONS}번까지 만들 수 있습니다. 저장된 브리핑을 확인해 주세요."

MarketStatus = Literal["latest", "delayed", "stale", "no_data"]


class GenerateRequest(BaseModel):
    regenerate: bool = Field(False, description="true면 오늘 브리핑이 있어도 새 버전을 만든다(기존 것은 그대로 보존)")
    locale: str | None = Field(None, max_length=MAX_LOCALE_LEN, description="ko-KR 기본, en-US, ja-JP, zh-CN. 없으면 계정 설정")
    timezone: str | None = Field(None, max_length=64, description="IANA 시간대. 없으면 계정 설정, 그다음 Asia/Seoul")
    use_llm: bool = Field(True, description="false면 LLM 요약을 쓰지 않고 코드 요약만 쓴다")


class BriefingRecord(BaseModel):
    id: int
    version: int
    local_date: str
    timezone: str
    locale: str
    generated_at: str
    data_as_of: str | None
    market_data_status: MarketStatus
    summary_source: Literal["llm", "code"]
    section_status: dict[str, str]
    tickers: list[dict[str, Any]]
    sources: list[dict[str, Any]]
    content: dict[str, Any]
    original_content: dict[str, Any] | None  # locale이 ko-KR이 아닐 때만(같은 계산 결과의 한국어 브리핑)
    date_group: DateGroup
    created: bool | None = None  # generate 응답에서만: 이번 요청에 새로 만들었는지


class BriefingItem(BaseModel):
    id: int
    version: int
    versions_on_date: int
    local_date: str
    local_time: str
    date_group: DateGroup
    generated_at: str
    locale: str
    data_as_of: str | None
    market_data_status: MarketStatus
    summary_source: Literal["llm", "code"]
    title: str | None
    headline: str | None
    summary: str | None
    section_status: dict[str, str]
    ticker_count: int
    detail_path: str


class BriefingHistory(BaseModel):
    items: list[BriefingItem]
    page: int
    size: int
    total: int
    has_next: bool
    total_pages: int
    timezone: str
    latest_only: bool
    date_from: str | None
    date_to: str | None


def _tz(name: str | None, user: CurrentUser):
    tz, _, _ = history_window(None, None, name or user.preferred_timezone)
    return tz


@router.post("/generate", response_model=BriefingRecord, responses={201: {"model": BriefingRecord}})
def generate_daily_briefing(body: GenerateRequest, response: Response, user: CurrentUser = Depends(get_current_user)) -> dict:
    """오늘(사용자 시간대) 브리핑을 만든다. 이미 있으면 다시 계산하지 않고 저장본을 200으로 돌려준다(created=false).
    regenerate=true면 새 버전을 만들어 201(기존 버전은 보존). 하루 최대 MAX_VERSIONS개(넘으면 409)."""
    rate_limit.check(rate_limit.daily_briefing, user.owner_key, "RATE_LIMIT_DAILY_BRIEFING_PER_MIN")
    tz = _tz(body.timezone, user)
    locale = resolve_locale(body.locale, user.preferred_locale)
    db = SessionLocal()
    try:
        try:
            row, created = store.generate(db, user.id, user.owner_key, locale, tz, regenerate=body.regenerate, use_llm=body.use_llm)
        except store.TooManyVersions:
            raise HTTPException(status_code=409, detail=TOO_MANY)
        response.status_code = 201 if created else 200
        return store.serialize(row, tz, created)
    finally:
        db.close()


@router.get("/current", response_model=BriefingRecord)
def current_daily_briefing(timezone: str | None = timezone_query(), user: CurrentUser = Depends(get_current_user)) -> dict:
    """오늘(사용자 시간대) 브리핑의 마지막 버전. 없으면 404(만들지 않는다)."""
    tz = _tz(timezone, user)
    db = SessionLocal()
    try:
        row = store.current(db, user.id, tz)
        if row is None:
            raise HTTPException(status_code=404, detail=NOT_GENERATED)
        return store.serialize(row, tz)
    finally:
        db.close()


@router.get("/history", response_model=BriefingHistory)
def daily_briefing_history(
    date_from: date | None = Query(None, description="이 날짜(브리핑 날짜 local_date)부터. YYYY-MM-DD"),
    date_to: date | None = Query(None, description="이 날짜까지(포함). YYYY-MM-DD"),
    timezone: str | None = timezone_query(),
    latest_only: bool = Query(True, description="true면 날짜마다 마지막 버전만, false면 모든 버전"),
    page: int = Query(1, ge=1, le=MAX_PAGE),
    size: int = Query(20, ge=1, le=MAX_SIZE),
    user: CurrentUser = Depends(get_current_user),
) -> dict:
    """내 브리핑 목록(날짜 최신순, 같은 날은 버전 최신순). 본문은 상세 조회로 본다."""
    tz, _, _ = history_window(date_from, date_to, timezone or user.preferred_timezone)
    db = SessionLocal()
    try:
        rows, total, counts = store.history(db, user.id, date_from, date_to, page, size, latest_only)
        items = [store.serialize_item(r, tz, counts.get(r.local_date, 1)) for r in rows]
    finally:
        db.close()
    return {"items": items, "page": page, "size": size, "total": total, **page_meta(total, size, (page - 1) * size),
            "timezone": tz.key, "latest_only": latest_only,
            "date_from": date_from.isoformat() if date_from else None, "date_to": date_to.isoformat() if date_to else None}


@router.get("/{briefing_id}", response_model=BriefingRecord)
def get_daily_briefing(briefing_id: int = Path(ge=1, le=BIGINT_MAX), timezone: str | None = timezone_query(),
                       user: CurrentUser = Depends(get_current_user)) -> dict:
    """저장된 브리핑 그대로(현재 시세로 다시 계산하지 않는다). 남의 브리핑이나 없는 id는 404."""
    tz = _tz(timezone, user)
    db = SessionLocal()
    try:
        row = store.get(db, user.id, briefing_id)
        if row is None:
            raise HTTPException(status_code=404, detail=NOT_FOUND)
        return store.serialize(row, tz)
    finally:
        db.close()
