"""기록 목록 API(리서치·진단 이력·통합 검색)가 같이 쓰는 날짜·시간대 파라미터 처리. 2026-11-05.

잘못된 값은 FastAPI 기본 검증 오류와 같은 형식의 422로 돌려준다. 인증 의존성이 먼저 풀리므로 토큰이 없으면 여전히 401이 먼저다.
"""

from datetime import date, datetime
from zoneinfo import ZoneInfo

from fastapi.exceptions import RequestValidationError

from app.history_dates import date_fields, resolve_timezone, utc_range


def _invalid(field: str, message: str, value, where: str = "query") -> RequestValidationError:
    return RequestValidationError([{"type": "value_error", "loc": (where, field), "msg": message, "input": value}])


def history_window(
    date_from: date | None, date_to: date | None, timezone_name: str | None, where: str = "query"
) -> tuple[ZoneInfo, datetime | None, datetime | None]:
    """(시간대, 시작 UTC 이상, 끝 UTC 미만). 모르는 시간대·거꾸로 된 날짜 범위는 422.
    where: 오류의 loc 첫 칸("query" | "body"). 2026-11-08(37차): 본문으로 시간대를 받는 POST /api/briefings/daily/generate가
    ["query", "timezone"]으로 보고하던 것을 ["body", "timezone"]으로 고쳤다."""
    try:
        tz = resolve_timezone(timezone_name)
    except ValueError as e:
        raise _invalid("timezone", str(e), timezone_name, where)
    try:
        start, end = utc_range(date_from, date_to, tz)
    except ValueError as e:
        raise _invalid("date_from", str(e), date_from.isoformat() if date_from else None, where)
    return tz, start, end


def with_dates(item: dict, tz: ZoneInfo) -> dict:
    """항목의 created_at_dt(UTC datetime)로 local_date·local_time·date_group을 붙인다."""
    return {**item, **date_fields(item["created_at_dt"], tz)}
