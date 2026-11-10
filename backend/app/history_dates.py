"""기록(리서치·진단) 목록의 날짜 처리: 시간대, 날짜 범위 → UTC 구간, 날짜 그룹. 2026-11-05.

DB의 created_at은 UTC(timestamptz)로 저장된 그대로 두고, 조회·표시할 때만 요청 시간대(기본 Asia/Seoul)로 바꾼다.

- date_from/date_to는 그 시간대의 달력 날짜다. date_to는 그날 끝까지 포함한다(다음 날 0시 미만).
- 날짜 그룹(DATE_GROUPS)은 그 시간대의 "오늘"을 기준으로 한 달력 날짜 차이로 정한다:
  today(0일), yesterday(1일), last_7_days(2~6일), last_30_days(7~29일), older(30일 이상).
  시계 차이로 미래 시각이 들어오면 today로 본다.
"""

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_TIMEZONE = "Asia/Seoul"
MAX_TIMEZONE_LEN = 64
DATE_GROUPS = ("today", "yesterday", "last_7_days", "last_30_days", "older")


def resolve_timezone(name: str | None) -> ZoneInfo:
    """IANA 시간대 이름(예: Asia/Seoul, America/New_York). 없거나 공백이면 기본값. 모르는 이름이면 ValueError."""
    name = (name or "").strip() or DEFAULT_TIMEZONE
    if len(name) > MAX_TIMEZONE_LEN:
        raise ValueError("시간대 이름이 너무 깁니다.")
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as e:
        raise ValueError(f"알 수 없는 시간대입니다: {name}") from e


def utc_range(date_from: date | None, date_to: date | None, tz: ZoneInfo) -> tuple[datetime | None, datetime | None]:
    """(시작 이상, 끝 미만) UTC 시각. 없는 쪽은 None. date_from이 date_to보다 늦으면 ValueError."""
    if date_from and date_to and date_from > date_to:
        raise ValueError("date_from이 date_to보다 늦습니다.")
    start = datetime.combine(date_from, time.min, tz).astimezone(timezone.utc) if date_from else None
    end = datetime.combine(date_to + timedelta(days=1), time.min, tz).astimezone(timezone.utc) if date_to else None
    return start, end


def now() -> datetime:
    """날짜 그룹의 기준 시각. 테스트가 바꿔 끼울 수 있게 함수로 둔다."""
    return datetime.now(timezone.utc)


def local_date(created_at: datetime, tz: ZoneInfo) -> date:
    return created_at.astimezone(tz).date()


def date_group(created_at: datetime, tz: ZoneInfo, current: datetime | None = None) -> str:
    days = (local_date(current or now(), tz) - local_date(created_at, tz)).days
    if days <= 0:
        return "today"
    if days == 1:
        return "yesterday"
    if days < 7:
        return "last_7_days"
    if days < 30:
        return "last_30_days"
    return "older"


def date_fields(created_at: datetime, tz: ZoneInfo, current: datetime | None = None) -> dict:
    """목록 항목에 붙이는 표시용 날짜: local_date(YYYY-MM-DD), local_time(HH:MM), date_group."""
    local = created_at.astimezone(tz)
    return {
        "local_date": local.date().isoformat(),
        "local_time": local.strftime("%H:%M"),
        "date_group": date_group(created_at, tz, current),
    }
