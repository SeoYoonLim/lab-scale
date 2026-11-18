"""평가 전용 KRX 거래일 계산(57차, 2026-11-15). 평가기(quality_judges·run_quality·forecast_check·metrics)가 같이 쓴다.

- 제품 코드(app.*)를 import하지 않는다(36·56차 원칙: 정답 계산은 독립 구현, 달력 데이터는 하나). DB·네트워크를 쓰지 않는다.
- 데이터: backend/app/data/krx_calendar.json(제품과 같은 파일). 파일을 읽을 때 형식을 따로 검사한다.
- 날짜는 KST 날짜, 시각은 KST "HH:MM" 문자열로 받는다.

날짜 상태(day_status):
- trading: 달력 범위 안의 평일이고 휴장일·미확정일이 아님. 특수 거래시간 날(2026-11-19)도 trading이다(하루 한 번 센다).
- weekend: 토·일(범위와 상관없이)
- holiday: 달력 휴장일, 또는 범위 밖 해의 고정 공휴일(fixed_annual: 날짜가 고정된 법정 공휴일·근로자의 날)
- unknown: 미확정일(unconfirmed, 예: 2026-12-31) 또는 범위 밖 평일(고정 공휴일 아님)

범위 밖·미확정 정책(57차): 평가기는 unknown을 거래일로도 휴장일로도 보지 않는다. 거래일 판정이 필요한 계산이 unknown을 만나면
CalendarUnknown을 낸다(평가를 멈추고 원인을 밝힌다). 잘못된 정답으로 채점하는 것보다 낫다. 제품(app.krx_calendar)은 화면 답을 위해
unknown을 "열릴 수 있는 날"로 세고 미확인 일수를 따로 알린다. 평가 입력 날짜(2023-10-30~2026-11-09)는 모두 범위 안이다.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path

CALENDAR_FILE = Path(__file__).resolve().parents[1] / "app" / "data" / "krx_calendar.json"
TRADING, WEEKEND, HOLIDAY, UNKNOWN = "trading", "weekend", "holiday", "unknown"
MAX_SCAN_DAYS = 60  # 직전·다음 거래일 탐색 한도(연휴가 이보다 길 수 없다)
_HM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class CalendarError(ValueError):
    """달력 파일 없음·JSON 오류·형식 오류·중복."""


class CalendarUnknown(LookupError):
    """달력으로 거래일 여부를 확정할 수 없는 날(미확정·범위 밖 평일)을 계산이 만났다."""

    def __init__(self, day: date, reason: str):
        self.day, self.reason = day, reason
        super().__init__(f"{day.isoformat()}: KRX 거래일 여부를 확정할 수 없습니다({reason}). app/data/krx_calendar.json을 갱신하세요")


@dataclass(frozen=True)
class EvalCalendar:
    start: date
    end: date
    holidays: frozenset
    unconfirmed: frozenset
    fixed_annual: frozenset  # (월, 일)
    regular_open: str = "09:00"
    regular_close: str = "15:30"
    special_open: dict = field(default_factory=dict)
    special_close: dict = field(default_factory=dict)


def _day(v, what: str) -> date:
    if not isinstance(v, str):
        raise CalendarError(f"{what}: 날짜가 문자열이 아닙니다({v!r})")
    try:
        d = date.fromisoformat(v)
    except ValueError:
        raise CalendarError(f"{what}: 날짜 형식 오류({v!r})") from None
    if d.isoformat() != v:  # 20261119 같은 다른 ISO 표기도 거부(YYYY-MM-DD만)
        raise CalendarError(f"{what}: 날짜는 YYYY-MM-DD여야 합니다({v!r})")
    return d


def _hm(v, what: str) -> str:
    if not isinstance(v, str) or not _HM.match(v):
        raise CalendarError(f"{what}: 시각은 HH:MM이어야 합니다({v!r})")
    return v


def parse(data: dict) -> EvalCalendar:
    """JSON -> EvalCalendar. 형식 오류·중복·범위 밖·주말 휴장일·거래일 아닌 특수일·개장 >= 마감은 CalendarError."""
    if not isinstance(data, dict) or "coverage" not in data:
        raise CalendarError("coverage가 없습니다")
    start, end = _day(data["coverage"].get("start"), "coverage.start"), _day(data["coverage"].get("end"), "coverage.end")
    if start > end:
        raise CalendarError(f"coverage 시작 {start} > 끝 {end}")
    holidays: set[date] = set()
    for h in data.get("holidays", []):
        d = _day(h.get("date"), "holidays")
        if not start <= d <= end:
            raise CalendarError(f"휴장일 {d}가 적용 범위 밖입니다")
        if d.weekday() >= 5:
            raise CalendarError(f"휴장일 {d}가 주말입니다")
        if d in holidays:
            raise CalendarError(f"휴장일 {d}가 두 번 있습니다")
        holidays.add(d)
    unconfirmed: set[date] = set()
    for u in data.get("unconfirmed", []):
        d = _day(u.get("date"), "unconfirmed")
        if d in unconfirmed or d in holidays:
            raise CalendarError(f"미확정일 {d}가 중복이거나 휴장일과 겹칩니다")
        unconfirmed.add(d)
    fixed = set()
    for f in data.get("fixed_annual", []):
        md = f.get("md")
        if not isinstance(md, str) or not re.match(r"^(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$", md):
            raise CalendarError(f"fixed_annual 형식 오류({md!r})")
        fixed.add((int(md[:2]), int(md[3:])))
    r_open, r_close = _hm(data.get("regular_open", "09:00"), "regular_open"), _hm(data.get("regular_close", "15:30"), "regular_close")
    if r_open >= r_close:
        raise CalendarError(f"regular_open {r_open} >= regular_close {r_close}")
    s_open, s_close = {}, {}
    for s in data.get("special_sessions", []):
        d = _day(s.get("date"), "special_sessions")
        if not start <= d <= end or d.weekday() >= 5 or d in holidays:
            raise CalendarError(f"특수 거래일 {d}가 범위 안의 거래일이 아닙니다")
        if d in s_close:
            raise CalendarError(f"특수 거래일 {d}가 두 번 있습니다")
        s_close[d] = _hm(s.get("close"), f"special_sessions {d} close")
        if "open" in s:
            s_open[d] = _hm(s["open"], f"special_sessions {d} open")
        if s_open.get(d, r_open) >= s_close[d]:
            raise CalendarError(f"특수 거래일 {d}의 개장 >= 마감")
    return EvalCalendar(start, end, frozenset(holidays), frozenset(unconfirmed), frozenset(fixed), r_open, r_close, s_open, s_close)


def load(path: Path | str | None = None) -> EvalCalendar:
    p = Path(path) if path is not None else CALENDAR_FILE
    try:
        text = p.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise CalendarError(f"달력 파일이 없습니다: {p}") from None
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise CalendarError(f"달력 JSON 오류: {p}: {e}") from None
    return parse(data)


@lru_cache(maxsize=1)
def calendar() -> EvalCalendar:
    return load()


def day_status(d: date, cal: EvalCalendar | None = None) -> str:
    cal = cal or calendar()
    if d.weekday() >= 5:
        return WEEKEND
    if d in cal.holidays:
        return HOLIDAY
    if d in cal.unconfirmed:
        return UNKNOWN
    if not cal.start <= d <= cal.end:
        return HOLIDAY if (d.month, d.day) in cal.fixed_annual else UNKNOWN
    return TRADING


def is_trading_day(d: date, cal: EvalCalendar | None = None) -> bool:
    """거래일이면 True, 주말·휴장일이면 False. 확정할 수 없는 날은 CalendarUnknown."""
    st = day_status(d, cal)
    if st == UNKNOWN:
        cal = cal or calendar()
        raise CalendarUnknown(d, "미확정 휴장일" if d in cal.unconfirmed else f"달력 범위({cal.start}~{cal.end}) 밖 평일")
    return st == TRADING


def close_hhmm(d: date, cal: EvalCalendar | None = None) -> str:
    """그날 정규장 마감 "HH:MM"(평소 15:30, 특수 거래일은 그 값. 2026-11-19 16:30). 시각만 돌려준다(거래일 판정 아님)."""
    cal = cal or calendar()
    return cal.special_close.get(d, cal.regular_close)


def open_hhmm(d: date, cal: EvalCalendar | None = None) -> str:
    cal = cal or calendar()
    return cal.special_open.get(d, cal.regular_open)


def _check_hhmm(hhmm: str) -> str:
    if not isinstance(hhmm, str) or not _HM.match(hhmm):
        raise ValueError(f"시각은 HH:MM이어야 합니다({hhmm!r})")
    return hhmm


def before_close(d: date, hhmm: str, cal: EvalCalendar | None = None) -> bool:
    """d가 거래일이고 hhmm이 그날 마감 전인가(오늘 행이 아직 종가가 아니다). 주말·휴장일은 False."""
    _check_hhmm(hhmm)
    return is_trading_day(d, cal) and hhmm < close_hhmm(d, cal)


def last_trading_day(d: date, cal: EvalCalendar | None = None) -> date:
    """d 이하의 가장 최근 거래일."""
    for _ in range(MAX_SCAN_DAYS):
        if is_trading_day(d, cal):
            return d
        d -= timedelta(days=1)
    raise CalendarError("거래일을 찾지 못했습니다(달력 데이터 확인)")


def previous_trading_day(d: date, cal: EvalCalendar | None = None) -> date:
    return last_trading_day(d - timedelta(days=1), cal)


def next_trading_day(d: date, cal: EvalCalendar | None = None) -> date:
    d += timedelta(days=1)
    for _ in range(MAX_SCAN_DAYS):
        if is_trading_day(d, cal):
            return d
        d += timedelta(days=1)
    raise CalendarError("거래일을 찾지 못했습니다(달력 데이터 확인)")


def nth_trading_day(d: date, n: int, cal: EvalCalendar | None = None) -> date:
    """d에서 n번째 거래일(d 제외). n > 0이면 뒤로, n < 0이면 앞으로. n = 0은 ValueError."""
    if n == 0:
        raise ValueError("n은 0이 아니어야 합니다")
    step = next_trading_day if n > 0 else previous_trading_day
    for _ in range(abs(n)):
        d = step(d, cal)
    return d


def trading_days(start: date, end: date, cal: EvalCalendar | None = None) -> list[date]:
    """start~end(양 끝 포함)의 거래일 목록. 그 사이에 확정할 수 없는 날이 있으면 CalendarUnknown."""
    out, d = [], start
    while d <= end:
        if is_trading_day(d, cal):
            out.append(d)
        d += timedelta(days=1)
    return out


def count_trading_days(start: date, end: date, cal: EvalCalendar | None = None) -> int:
    return len(trading_days(start, end, cal))


def trading_days_after(latest: date, reference: date, cal: EvalCalendar | None = None) -> int:
    """latest 다음 날 ~ reference(포함)의 거래일 수 = latest가 reference보다 몇 거래일 뒤처졌는지. latest >= reference면 0."""
    if latest >= reference:
        return 0
    return count_trading_days(latest + timedelta(days=1), reference, cal)


def expected_trading_day(d: date, hhmm: str = "20:00", cal: EvalCalendar | None = None) -> date:
    """KST d·hhmm에 종가가 있어야 할 가장 최근 거래일. 거래일 마감 전이면 직전 거래일, 주말·휴장일이면 그 앞 거래일."""
    _check_hhmm(hhmm)
    t = last_trading_day(d, cal)
    if t == d and hhmm < close_hhmm(d, cal):
        t = previous_trading_day(d, cal)
    return t
