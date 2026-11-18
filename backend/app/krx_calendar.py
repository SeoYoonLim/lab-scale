"""한국거래소(KRX) 정규시장 거래일 달력(2026-11-08, 36차). 데이터는 app/data/krx_calendar.json(버전·출처·적용 범위)이고 이 모듈은 읽기만 한다.

날짜 상태(day_status):
- trading: 달력 범위 안의 평일이고 휴장일이 아님
- weekend: 토·일(KRX 정규시장은 주말에 열지 않는다. 범위 밖에도 같다)
- holiday: 달력에 있는 휴장일(법정 공휴일·대체공휴일·선거일·임시공휴일·근로자의 날·연말 휴장 등)
- unknown: 달력 범위 밖의 평일, 또는 아직 공지되지 않아 확정하지 못한 날(unconfirmed, 예: 공지 전의 연말 휴장일. 58차 현재 없음).
  범위 밖이라도 날짜가 고정된 법정 공휴일·근로자의 날(fixed_annual)은 holiday다(설·추석·대체공휴일·선거일은 해마다 달라 unknown)

unknown은 거래일로 단정하지 않는다. 다만 "기대 거래일"·"지연 영업일"처럼 날짜나 정수가 필요한 계산에서는 거래일일 수 있는 날로 센다
(오래된 데이터를 최신이라고 말하지 않는 쪽, 지연을 크게 잡는 쪽). 그 대신 lag()·unknown_days()가 미확인 일수를 따로 돌려주고,
화면 문장(app.quote_freshness)과 운영 보고서(app.data_monitor)는 이 값으로 "달력 범위 밖/미확정"이라고 밝힌다.

장 마감: 정규시장 15:30(KST). special_sessions에 마감 시각이 다른 날(수능일 등)을 KRX 공지 뒤 넣으면 그날만 그 시각을 쓴다
(2026-11-19 수능일 16:30, 55차). close_time()은 정규시장 마감이다(장후 시간외·애프터마켓 종료가 아니다).
장 마감 판정이 필요한 코드는 15:30 상수 대신 close_time(날짜)를 쓴다(55차: 지수 확정·장중 판정·기대 거래일·ETF 최신일·뉴스 반응 기준일).
calendar()는 프로세스 안에서 한 번만 읽는다(lru_cache): JSON을 바꾸면 실행 중인 서버는 재시작해야 반영된다.

장 개장(56차): 정규시장 09:00(KST, regular_open). special_sessions 항목에 open을 넣으면 그날만 그 시각(2026-11-19 10:00), 없으면 09:00.
- open_time()/close_time()이 개장·마감을 조회하는 유일한 함수다. 다른 모듈은 시각 상수를 다시 두지 않는다.
- market_phase(now)는 달력만으로 정하는 장 단계(non_trading_day / pre_market / intraday / closed)다. "종가 확정"은 달력이 정하지 않는다
  (지수는 app.index_confirm이 실제 값 대조로 정한다).
- session_is_open()은 "오늘 종가가 아직 없다(마감 전)"는 뜻이다. 장 시작 전도 True다(36차부터 같은 의미, 공개 API의
  intraday_excluded·일일 브리핑 intraday가 이 값이다). 정규장 진행 중만 알려면 market_phase(now) == INTRADAY를 쓴다.

뉴스·공시는 이 달력을 쓰지 않는다(app.data_status의 평일 함수, docs/DATA_QUALITY_AND_TRADING_CALENDAR.md).
환율(56차 조사): 운영 보고서의 환율 기대일은 이 달력이 아니라 평일 정책이다(app.data_monitor.expected_weekday_close).
브리핑 화면은 시세·지수와 같은 기준일을 보이려고 환율 오늘 행에도 drop_open_session(이 달력)을 쓴다.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")
DATA_PATH = Path(__file__).resolve().parent / "data" / "krx_calendar.json"

TRADING, WEEKEND, HOLIDAY, UNKNOWN = "trading", "weekend", "holiday", "unknown"
_MAX_SCAN_DAYS = 60  # 직전·다음 거래일을 찾을 때 최대 탐색 일수(연휴가 이보다 길 수 없다)


@dataclass(frozen=True)
class Calendar:
    version: str
    updated: date
    start: date
    end: date
    regular_close: time
    holidays: dict[date, str]
    unconfirmed: dict[date, str]
    special_close: dict[date, time]
    fixed_annual: dict[tuple[int, int], str]  # 범위 밖 해에 쓰는 고정 휴장일(월, 일)
    regular_open: time = time(9, 0)  # 56차. 뒤에 두어 기존 Calendar(**...) 호출이 그대로 동작한다
    special_open: dict[date, time] = field(default_factory=dict)  # 56차. open이 있는 특수일만


def _hm(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))


_HM_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def _hm_checked(v, what: str) -> time:
    if not isinstance(v, str) or not _HM_RE.match(v):
        raise ValueError(f"{what} 형식이 HH:MM이 아닙니다: {v!r}")
    return _hm(v)


def _special_sessions(items: list[dict], start: date, end: date, holidays: dict[date, str],
                      regular_open: time = time(9, 0)) -> tuple[dict[date, time], dict[date, time]]:
    """special_sessions -> ({날짜: 정규시장 마감}, {날짜: 정규시장 개장(open이 있는 날만)}).
    close는 정규시장 마감(종가 단일가 체결) 시각이다. 애프터마켓·시간외 종료가 아니다. open은 정규시장 개장(장전 시간외가 아니다).
    55차: 중복·주말·휴장일·범위 밖 날짜·HH:MM 형식 오류는 ValueError(같은 날짜가 두 번이면 뒤 값이 조용히 이기던 것을 막는다).
    56차: open 형식 오류, 그날 개장(open 또는 regular_open) >= 마감이면 ValueError."""
    out: dict[date, time] = {}
    opens: dict[date, time] = {}
    for s in items:
        d = date.fromisoformat(s["date"])
        if not start <= d <= end:
            raise ValueError(f"특수 마감일 {d}가 적용 범위({start}~{end}) 밖입니다")
        if d.weekday() >= 5 or d in holidays:
            raise ValueError(f"특수 마감일 {d}가 거래일이 아닙니다(주말·휴장일)")
        if d in out:
            raise ValueError(f"특수 마감일 {d}가 두 번 있습니다")
        out[d] = _hm_checked(s.get("close"), f"특수 마감일 {d}의 close")
        if "open" in s:
            opens[d] = _hm_checked(s["open"], f"특수 마감일 {d}의 open")
        if opens.get(d, regular_open) >= out[d]:
            raise ValueError(f"특수 마감일 {d}의 개장 {opens.get(d, regular_open)}이 마감 {out[d]}보다 늦거나 같습니다")
    return out, opens


def parse(data: dict) -> Calendar:
    """JSON -> Calendar. 형식 오류(범위 밖 휴장일, 주말 휴장일, 중복)는 ValueError(달력 데이터를 고칠 때 바로 드러나게)."""
    start, end = date.fromisoformat(data["coverage"]["start"]), date.fromisoformat(data["coverage"]["end"])
    holidays: dict[date, str] = {}
    for h in data.get("holidays", []):
        d = date.fromisoformat(h["date"])
        if not start <= d <= end:
            raise ValueError(f"휴장일 {d}가 적용 범위({start}~{end}) 밖입니다")
        if d.weekday() >= 5:
            raise ValueError(f"휴장일 {d}가 주말입니다(주말은 따로 적지 않는다)")
        if d in holidays:
            raise ValueError(f"휴장일 {d}가 두 번 있습니다")
        holidays[d] = h["name"]
    unconfirmed = {date.fromisoformat(u["date"]): u["name"] for u in data.get("unconfirmed", [])}
    if overlap := set(unconfirmed) & set(holidays):
        raise ValueError(f"확정·미확정 휴장일이 겹칩니다: {sorted(overlap)}")
    regular_open = _hm_checked(data.get("regular_open", "09:00"), "regular_open")
    regular_close = _hm_checked(data.get("regular_close", "15:30"), "regular_close")
    if regular_open >= regular_close:
        raise ValueError(f"regular_open {regular_open}이 regular_close {regular_close}보다 늦거나 같습니다")
    special, special_open = _special_sessions(data.get("special_sessions", []), start, end, holidays, regular_open)
    fixed = {(int(f["md"][:2]), int(f["md"][3:])): f["name"] for f in data.get("fixed_annual", [])}
    # 범위 안에서는 고정 휴장일이 평일이면 반드시 휴장일 목록에 있어야 한다(목록을 고칠 때 빠뜨리지 않게)
    d = start
    while d <= end:
        if (d.month, d.day) in fixed and d.weekday() < 5 and d not in holidays:
            raise ValueError(f"고정 휴장일 {d}({fixed[(d.month, d.day)]})가 휴장일 목록에 없습니다")
        d += timedelta(days=1)
    return Calendar(data["version"], date.fromisoformat(data["updated"]), start, end, regular_close,
                    holidays, unconfirmed, special, fixed, regular_open, special_open)


@lru_cache(maxsize=1)
def calendar() -> Calendar:
    return parse(json.loads(DATA_PATH.read_text(encoding="utf-8")))


def day_status(d: date, cal: Calendar | None = None) -> str:
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


def is_trading_day(d: date, cal: Calendar | None = None) -> bool | None:
    """True(거래일) / False(주말·휴장일) / None(확인할 수 없음)."""
    st = day_status(d, cal)
    return None if st == UNKNOWN else st == TRADING


def is_holiday(d: date, cal: Calendar | None = None) -> bool:
    return day_status(d, cal) == HOLIDAY


def holiday_name(d: date, cal: Calendar | None = None) -> str | None:
    cal = cal or calendar()
    if d in cal.holidays:
        return cal.holidays[d]
    if d in cal.unconfirmed:
        return f"{cal.unconfirmed[d]}(미확정)"
    if not cal.start <= d <= cal.end and d.weekday() < 5:
        return cal.fixed_annual.get((d.month, d.day))
    return None


def _possible(d: date, cal: Calendar) -> bool:
    """거래일이거나 거래일일 수 있는 날(trading 또는 unknown)."""
    return day_status(d, cal) in (TRADING, UNKNOWN)


def last_trading_day(d: date, cal: Calendar | None = None) -> date:
    """d 이하의 가장 최근 거래일(미확인 평일은 거래일일 수 있는 날로 본다)."""
    cal = cal or calendar()
    for _ in range(_MAX_SCAN_DAYS):
        if _possible(d, cal):
            return d
        d -= timedelta(days=1)
    raise ValueError("거래일을 찾지 못했습니다(달력 데이터 확인)")


def previous_trading_day(d: date, cal: Calendar | None = None) -> date:
    """d보다 앞의 가장 가까운 거래일."""
    return last_trading_day(d - timedelta(days=1), cal)


def next_trading_day(d: date, cal: Calendar | None = None) -> date:
    """d보다 뒤의 가장 가까운 거래일."""
    cal = cal or calendar()
    d += timedelta(days=1)
    for _ in range(_MAX_SCAN_DAYS):
        if _possible(d, cal):
            return d
        d += timedelta(days=1)
    raise ValueError("거래일을 찾지 못했습니다(달력 데이터 확인)")


def close_time(d: date, cal: Calendar | None = None) -> time:
    """그날 정규시장 마감(KST). 특수일이 아니면 regular_close(15:30)."""
    cal = cal or calendar()
    return cal.special_close.get(d, cal.regular_close)


def open_time(d: date, cal: Calendar | None = None) -> time:
    """그날 정규시장 개장(KST, 56차). 특수일에 open이 있으면 그 값, 없으면 regular_open(09:00)."""
    cal = cal or calendar()
    return cal.special_open.get(d, cal.regular_open)


NON_TRADING_DAY, PRE_MARKET, INTRADAY, CLOSED = "non_trading_day", "pre_market", "intraday", "closed"


def market_phase(now: datetime, cal: Calendar | None = None) -> str:
    """달력만으로 정한 지금의 장 단계(56차, 내부용). 휴장일·주말 non_trading_day, 개장 전 pre_market, 개장~마감 전 intraday,
    마감 뒤 closed. 미확인 평일(unknown)은 거래일일 수 있는 날로 보고 기본 시각을 쓴다. 종가 확정 여부는 여기서 정하지 않는다."""
    cal = cal or calendar()
    now = _kst(now)
    d = now.date()
    if not _possible(d, cal):
        return NON_TRADING_DAY
    if now.time() < open_time(d, cal):
        return PRE_MARKET
    if now.time() < close_time(d, cal):
        return INTRADAY
    return CLOSED


def _kst(now: datetime) -> datetime:
    return (now if now.tzinfo else now.replace(tzinfo=KST)).astimezone(KST)


def session_is_open(now: datetime, cal: Calendar | None = None) -> bool:
    """정규장 마감 전의 거래일(또는 미확인 평일)이면 True. 휴장일·주말·마감 뒤는 False.
    장 시작 전도 True다(오늘 종가가 아직 없다는 뜻. market_phase의 pre_market·intraday). 정규장 진행 중만은 market_phase로 본다."""
    cal = cal or calendar()
    now = _kst(now)
    return _possible(now.date(), cal) and now.time() < close_time(now.date(), cal)


def expected_trading_day(now: datetime, cal: Calendar | None = None) -> date:
    """지금 종가가 있어야 할 가장 최근 거래일. 오늘이 거래일이고 마감 전이면 직전 거래일, 휴장일·주말이면 직전 거래일."""
    cal = cal or calendar()
    now = _kst(now)
    d = last_trading_day(now.date(), cal)
    if d == now.date() and now.time() < close_time(d, cal):
        d = previous_trading_day(d, cal)
    return d


def trading_days(start: date, end: date, cal: Calendar | None = None, include_unknown: bool = True) -> list[date]:
    """start~end(포함)의 거래일 목록. include_unknown이면 미확인 평일도 넣는다."""
    cal = cal or calendar()
    out, d = [], start
    while d <= end:
        st = day_status(d, cal)
        if st == TRADING or (include_unknown and st == UNKNOWN):
            out.append(d)
        d += timedelta(days=1)
    return out


def unknown_days(start: date, end: date, cal: Calendar | None = None) -> list[date]:
    """start~end(포함) 중 달력으로 확인할 수 없는 평일."""
    cal = cal or calendar()
    return [d for d in trading_days(start, end, cal) if day_status(d, cal) == UNKNOWN]


@dataclass(frozen=True)
class Lag:
    days: int  # latest 뒤 ~ reference까지의 거래일 수(미확인 평일 포함, 상한)
    unknown: int  # 그중 달력으로 확인할 수 없는 날 수(0이면 확정값)

    @property
    def certain(self) -> bool:
        return self.unknown == 0


def lag(latest: date, reference: date, cal: Calendar | None = None) -> Lag:
    """latest가 reference보다 몇 거래일 뒤처졌는지. latest >= reference이면 0."""
    if latest >= reference:
        return Lag(0, 0)
    days = trading_days(latest + timedelta(days=1), reference, cal)
    return Lag(len(days), sum(1 for d in days if day_status(d, cal or calendar()) == UNKNOWN))


def info() -> dict:
    """운영 보고서·문서용 달력 정보."""
    cal = calendar()
    return {"version": cal.version, "updated": cal.updated.isoformat(), "coverage_start": cal.start.isoformat(),
            "coverage_end": cal.end.isoformat(), "holidays": len(cal.holidays),
            "unconfirmed": [d.isoformat() for d in sorted(cal.unconfirmed)], "source_file": "app/data/krx_calendar.json"}
