"""KRX 거래일별 분봉 세션 일정(2026-11-14, 54차). 데이터: app/data/krx_session_schedule.json. DB·네트워크 없음.

- 정규 일정(default)과 날짜별 변경(overrides). 변경일은 날짜 전체 일정을 적는다(부분 상속 없음).
- 시각은 분봉 **시작 시각**(KST, 양 끝 포함)이다. 예: 정규 일정의 장후 종가매매 15:40~15:59 = 15:40:00~16:00:00 체결.
- 상태: data_verified(실제 분봉으로 검증) | announced(발표로 등록, 실제 분봉 검증 전).
- resolve(d)가 None과 사유를 돌려주는 날(휴장일, 달력 범위 밖, 달력에는 특수 마감이 있는데 일정이 등록되지 않은 날)은
  세션을 분류하지 않는다(app/session_bars.py가 그 종목-날짜를 거부).
- KRX만 다룬다. NXT 일정은 없다(분봉 원천이 KRX 체결만 담는다, 50·53차).

app/krx_calendar.py(휴장일·마감 시각, 기존 서비스가 쓰는 달력)와의 관계: 거래일 여부는 달력에서, 세션 시각은 이 일정에서 읽는다.
두 파일의 마감 시각이 다른 날은 calendar_mismatches()로 드러낸다(2026-11-19: 이 일정 16:30, 달력 15:30 — 54차 보고서 16절).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from functools import lru_cache
from pathlib import Path

from app import krx_calendar

PATH = Path(__file__).resolve().parent / "data" / "krx_session_schedule.json"
STATUSES = ("data_verified", "announced")
ASSET_TYPES = ("stock", "etf")
COMPLETE_GRACE = timedelta(minutes=10)  # 마지막 세션의 마지막 봉이 끝난 뒤 이만큼 지나야 그날 분봉이 끝난 것으로 본다


@dataclass(frozen=True)
class Window:
    start: str  # HHMM(봉 시작)
    end: str

    def __contains__(self, hm: str) -> bool:
        return self.start <= hm <= self.end


@dataclass(frozen=True)
class DaySchedule:
    day: date
    market: str
    kind: str  # default | override
    status: str
    label: str
    sources: tuple[str, ...]
    regular: Window
    closing_at: str  # 종가 단일가 봉 HHMM
    late_window: Window | None  # 종가 단일가 봉이 없을 때만 보는 지연 창(관찰로 검증된 일정만)
    post_close: Window | None
    after_market: Window | None
    after_market_asset_types: frozenset[str]
    after_market_from: date | None

    def after_market_for(self, asset_type: str | None) -> Window | None:
        """그 자산의 애프터마켓 창. 대상이 아니거나 개장 전이면 None(자산 구분을 모르면 None)."""
        if self.after_market is None or asset_type not in self.after_market_asset_types:
            return None
        if self.after_market_from is not None and self.day < self.after_market_from:
            return None
        return self.after_market

    @property
    def regular_close(self) -> time:
        """정규시장 마감(= 종가 단일가 체결 시각). krx_calendar.close_time과 대조한다."""
        return _t(self.closing_at)

    def complete_after(self) -> datetime:
        """그날 분봉이 모두 끝난 시각(마지막 세션 마지막 봉 + 1분 + COMPLETE_GRACE, KST). 정규 일정은 20:10."""
        last = max(w.end for w in (self.regular, self.post_close, self.after_market) if w is not None)
        last = max(last, self.closing_at)
        return datetime.combine(self.day, _t(last), tzinfo=krx_calendar.KST) + timedelta(minutes=1) + COMPLETE_GRACE

    def describe(self) -> dict:
        return {"date": self.day.isoformat(), "kind": self.kind, "status": self.status, "label": self.label}


def _t(hm: str) -> time:
    return time(int(hm[:2]), int(hm[2:]))


def _hm(s: str, where: str) -> str:
    try:
        h, m = s.split(":")
        t = time(int(h), int(m))
    except (ValueError, AttributeError):
        raise ValueError(f"{where}: 시각 형식이 HH:MM이 아닙니다: {s!r}")
    return f"{t.hour:02d}{t.minute:02d}"


def _window(d: dict | None, where: str) -> Window | None:
    if d is None:
        return None
    w = Window(_hm(d["start"], where), _hm(d["end"], where))
    if w.start > w.end:
        raise ValueError(f"{where}: 시작 {d['start']}이 끝 {d['end']}보다 늦습니다")
    return w


@dataclass(frozen=True)
class Schedule:
    version: str
    market: str
    default: dict
    overrides: dict[date, dict]


def _validate_block(b: dict, where: str) -> dict:
    """일정 한 묶음 검증 -> 정규화된 dict. 세션이 시간 순서대로 겹치지 않아야 한다."""
    if b.get("status") not in STATUSES:
        raise ValueError(f"{where}: 상태가 {STATUSES} 중 하나가 아닙니다: {b.get('status')!r}")
    sources = tuple(b.get("sources") or ())
    if not sources or not all(isinstance(s, str) and s.startswith("https://") for s in sources):
        raise ValueError(f"{where}: 공식 근거 URL(sources, https)이 없습니다")
    regular = _window(b["regular"], f"{where}.regular")
    ca = b["closing_auction"]
    closing_at = _hm(ca["at"], f"{where}.closing_auction")
    late = _window(ca.get("late_window"), f"{where}.closing_auction.late_window")
    post = _window(b.get("post_close"), f"{where}.post_close")
    am = b.get("after_market")
    after = _window(am, f"{where}.after_market")
    types = frozenset((am or {}).get("asset_types") or ())
    if types - set(ASSET_TYPES):
        raise ValueError(f"{where}.after_market: 알 수 없는 자산 구분 {sorted(types - set(ASSET_TYPES))}")
    if after is not None and not types:
        raise ValueError(f"{where}.after_market: 대상 자산(asset_types)이 없습니다")
    seq = [("regular", regular), ("closing_auction", Window(closing_at, closing_at))]
    if late:
        seq.append(("late_window", late))
    seq += [(n, w) for n, w in (("post_close", post), ("after_market", after)) if w]
    for (n1, w1), (n2, w2) in zip(seq, seq[1:]):
        if not w1.end < w2.start:
            raise ValueError(f"{where}: {n1}({w1.start}~{w1.end})와 {n2}({w2.start}~{w2.end})가 겹치거나 순서가 틀립니다")
    eff = (am or {}).get("effective_from")
    return {"status": b["status"], "label": b.get("label", ""), "sources": sources, "regular": regular, "closing_at": closing_at,
            "late_window": late, "post_close": post, "after_market": after, "after_market_asset_types": types,
            "after_market_from": date.fromisoformat(eff) if eff else None}


def parse(data: dict, cal: krx_calendar.Calendar | None = None) -> Schedule:
    """JSON -> Schedule. 형식·순서·근거·날짜 오류는 ValueError(일정을 고칠 때 바로 드러나게)."""
    cal = cal or krx_calendar.calendar()
    if data.get("market") != "KRX":
        raise ValueError("market은 KRX만 지원합니다(NXT 일정은 넣지 않는다)")
    default = _validate_block(data["default"], "default")
    overrides: dict[date, dict] = {}
    for o in data.get("overrides", []):
        d = date.fromisoformat(o["date"])
        where = f"overrides[{d}]"
        if d in overrides:
            raise ValueError(f"{where}: 같은 날짜가 두 번 있습니다")
        if not cal.start <= d <= cal.end:
            raise ValueError(f"{where}: KRX 달력 범위({cal.start}~{cal.end}) 밖입니다")
        if d.weekday() >= 5 or d in cal.holidays:
            raise ValueError(f"{where}: 휴장일·주말에는 일정을 둘 수 없습니다")
        if not o.get("reason"):
            raise ValueError(f"{where}: 변경 사유(reason)가 없습니다")
        overrides[d] = _validate_block(o, where)
    return Schedule(str(data.get("version", "")), data["market"], default, overrides)


@lru_cache(maxsize=1)
def schedule() -> Schedule:
    return parse(json.loads(PATH.read_text(encoding="utf-8")))


def resolve(d: date, cal: krx_calendar.Calendar | None = None, sched: Schedule | None = None) -> tuple[DaySchedule | None, str | None]:
    """(그날 일정, None) 또는 (None, 사유). 사유: non_trading_day | calendar_unknown | schedule_unknown."""
    cal = cal or krx_calendar.calendar()
    sched = sched or schedule()
    st = krx_calendar.is_trading_day(d, cal)
    if st is False:
        return None, "non_trading_day"
    if st is None:
        return None, "calendar_unknown"
    if d in sched.overrides:
        b, kind = sched.overrides[d], "override"
    elif d in cal.special_close:  # 달력에는 특수 마감이 있는데 세션 일정이 없다: 시각을 모르므로 분류하지 않는다
        return None, "schedule_unknown"
    else:
        b, kind = sched.default, "default"
    return DaySchedule(day=d, market=sched.market, kind=kind, **b), None


def calendar_mismatches(cal: krx_calendar.Calendar | None = None, sched: Schedule | None = None) -> list[dict]:
    """세션 일정의 정규 마감과 krx_calendar.close_time이 다른 날(변경일 기준)."""
    cal = cal or krx_calendar.calendar()
    sched = sched or schedule()
    out = []
    for d in sorted(sched.overrides):
        ds, _ = resolve(d, cal, sched)
        if ds is not None and ds.regular_close != krx_calendar.close_time(d, cal):
            out.append({"date": d.isoformat(), "schedule_close": ds.regular_close.strftime("%H:%M"),
                        "calendar_close": krx_calendar.close_time(d, cal).strftime("%H:%M")})
    return out
