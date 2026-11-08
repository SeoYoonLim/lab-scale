"""한국거래소(KRX) 정규시장 거래일 달력(2026-11-08, 36차). 데이터는 app/data/krx_calendar.json(버전·출처·적용 범위)이고 이 모듈은 읽기만 한다.

날짜 상태(day_status):
- trading: 달력 범위 안의 평일이고 휴장일이 아님
- weekend: 토·일(KRX 정규시장은 주말에 열지 않는다. 범위 밖에도 같다)
- holiday: 달력에 있는 휴장일(법정 공휴일·대체공휴일·선거일·임시공휴일·근로자의 날·연말 휴장 등)
- unknown: 달력 범위 밖의 평일, 또는 아직 공지되지 않아 확정하지 못한 날(unconfirmed, 예: 2026-12-31 연말 휴장).
  범위 밖이라도 날짜가 고정된 법정 공휴일·근로자의 날(fixed_annual)은 holiday다(설·추석·대체공휴일·선거일은 해마다 달라 unknown)

unknown은 거래일로 단정하지 않는다. 다만 "기대 거래일"·"지연 영업일"처럼 날짜나 정수가 필요한 계산에서는 거래일일 수 있는 날로 센다
(오래된 데이터를 최신이라고 말하지 않는 쪽, 지연을 크게 잡는 쪽). 그 대신 lag()·unknown_days()가 미확인 일수를 따로 돌려주고,
화면 문장(app.quote_freshness)과 운영 보고서(app.data_monitor)는 이 값으로 "달력 범위 밖/미확정"이라고 밝힌다.

장 마감: 정규시장 15:30(KST). special_sessions에 마감 시각이 다른 날(수능일 등)을 KRX 공지 뒤 넣으면 그날만 그 시각을 쓴다.
환율·뉴스·공시는 이 달력을 쓰지 않는다(app.data_status의 평일 함수, docs/DATA_QUALITY_AND_TRADING_CALENDAR.md).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
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


def _hm(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))


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
    special = {date.fromisoformat(s["date"]): _hm(s["close"]) for s in data.get("special_sessions", [])}
    fixed = {(int(f["md"][:2]), int(f["md"][3:])): f["name"] for f in data.get("fixed_annual", [])}
    # 범위 안에서는 고정 휴장일이 평일이면 반드시 휴장일 목록에 있어야 한다(목록을 고칠 때 빠뜨리지 않게)
    d = start
    while d <= end:
        if (d.month, d.day) in fixed and d.weekday() < 5 and d not in holidays:
            raise ValueError(f"고정 휴장일 {d}({fixed[(d.month, d.day)]})가 휴장일 목록에 없습니다")
        d += timedelta(days=1)
    return Calendar(data["version"], date.fromisoformat(data["updated"]), start, end, _hm(data.get("regular_close", "15:30")),
                    holidays, unconfirmed, special, fixed)


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
    cal = cal or calendar()
    return cal.special_close.get(d, cal.regular_close)


def _kst(now: datetime) -> datetime:
    return (now if now.tzinfo else now.replace(tzinfo=KST)).astimezone(KST)


def session_is_open(now: datetime, cal: Calendar | None = None) -> bool:
    """정규장 마감 전의 거래일(또는 미확인 평일)이면 True. 휴장일·주말·마감 뒤는 False."""
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
