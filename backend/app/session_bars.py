"""분봉을 KRX 거래 세션으로 분류하고 세션별로 집계한다(2026-11-14, 53차). 순수 함수, DB·네트워크 없음.

근거: docs/SESSION_MARKET_DATA_IMPLEMENTATION_REHEARSAL_53.md 3절(보존 분봉 1,192,628개·종목-날짜 2,450개 전수 조사).
- 원천: 네이버 국내 분봉(app.session_ohlcv.MINUTE_URL), KRX 체결만. `localDateTime`은 KST **봉 시작 시각**이다
  (첫 봉 09:00, 장후 종가매매 첫 봉 15:40, 애프터마켓 첫 봉 16:00, 마지막 봉 19:59, 초는 항상 00).
- 세션(정규 일정 15:30 마감 날만):
  regular          09:00~15:20 봉. 연속매매(시가 단일가 체결은 09:00 봉에 섞여 나눌 수 없다). 15:20 봉은 15:20:00 직후 체결
  closing_auction  15:30 봉 하나. 15:30 봉이 없으면 15:31~15:39의 봉 하나(관찰: 15:32). 두 경우 모두 시가=고가=저가=종가여야 한다
  post_close       15:40~15:59 봉(장후 시간외 종가매매). 모든 봉이 시가=고가=저가=종가=그날 종가 단일가 가격이어야 한다
  after_market     16:00~19:59 봉(KRX 애프터마켓). 주식, 2026-09-14 이후만
- 위에 맞지 않는 봉(09:00 전, 15:21~15:29, 조건을 못 맞춘 15:30~15:59 봉, ETF의 16시 이후, 20:00 이후)은 분류하지 않는다.
  그런 봉이 하나라도 있으면 그 종목-날짜 전체를 거부한다(일부 세션만 저장하면 하루가 완전한 것처럼 보이기 때문).
- 공식 종가와 같은지는 분류에 쓰지 않는다(분류 뒤 대조만 한다). NXT 세션은 원천에 없어 만들지 않는다.
- 특수 일정(달력의 마감 시각이 15:30이 아닌 날, 수능일 등)은 규칙이 검증되지 않아 거부한다. 달력에 없는 특수일은
  15:21~15:29 봉 등이 생겨 위 규칙에서 거부된다.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation

from app import krx_calendar
from app.official_close import AFTER_MARKET_START

RULES_VERSION = 1
VENUE_KRX = "KRX"
REGULAR, CLOSING_AUCTION, POST_CLOSE, AFTER_MARKET = "regular", "closing_auction", "post_close", "after_market"
SESSIONS = (REGULAR, CLOSING_AUCTION, POST_CLOSE, AFTER_MARKET)
KST = krx_calendar.KST
REGULAR_CLOSE = time(15, 30)
DAY_COMPLETE_AT = time(20, 10)  # app.session_ohlcv.DAY_COMPLETE_AT과 같다
PRICE_KEYS = ("openPrice", "highPrice", "lowPrice", "currentPrice")
VOLUME_KEY = "accumulatedTradingVolume"  # 이름과 달리 그 분의 거래량(50차)


@dataclass(frozen=True)
class Bar:
    at: datetime  # KST, 봉 시작
    hm: str
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int

    @property
    def flat(self) -> bool:
        return self.open == self.high == self.low == self.close


@dataclass(frozen=True)
class SessionAgg:
    session: str
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int
    bar_count: int
    first_bar_at: datetime
    last_bar_at: datetime

    def data_key(self) -> tuple:
        """충돌 판정에 쓰는 값(출처·수집 시각 제외)."""
        return (self.open, self.high, self.low, self.close, self.volume, self.bar_count, self.first_bar_at, self.last_bar_at)


@dataclass
class DayResult:
    day: date
    status: str = "ok"  # ok | rejected
    reasons: list[str] = field(default_factory=list)
    sessions: dict[str, SessionAgg] = field(default_factory=dict)
    unclassified: list[str] = field(default_factory=list)  # HHMM
    flags: list[str] = field(default_factory=list)  # late_closing_auction | no_closing_auction

    def reject(self, reason: str) -> None:
        self.status = "rejected"
        if reason not in self.reasons:
            self.reasons.append(reason)


# ---------- 파싱 ----------

def load_rows(data: bytes) -> list:
    """원본 파일 바이트 -> 목록. 소수는 Decimal로 읽어 원본 숫자를 그대로 보존한다(수정주가 ETF는 소수 12자리까지 관찰)."""
    return json.loads(data.decode("utf-8"), parse_float=Decimal)


def _price(v) -> Decimal | None:
    if isinstance(v, bool) or not isinstance(v, (int, Decimal, float)):
        return None
    try:
        d = Decimal(str(v)) if isinstance(v, float) else Decimal(v)
    except InvalidOperation:
        return None
    return d if d.is_finite() and d > 0 else None


def parse_bars(rows) -> tuple[dict[date, list[Bar]], dict[date, list[str]], list[str]]:
    """(날짜별 봉, 날짜별 문제, 날짜를 알 수 없는 문제). 문제가 있는 날짜는 분류에서 거부한다."""
    by: dict[date, list[Bar]] = {}
    problems: dict[date, list[str]] = {}
    orphan: list[str] = []
    if not isinstance(rows, list):
        return by, problems, ["not_a_list"]
    for i, r in enumerate(rows):
        ts = str(r.get("localDateTime", "")) if isinstance(r, dict) else ""
        try:
            if len(ts) != 14 or not ts.isdigit():
                raise ValueError
            at = datetime.strptime(ts, "%Y%m%d%H%M%S").replace(tzinfo=KST)
        except ValueError:
            orphan.append(f"bad_timestamp#{i}")
            continue
        d = at.date()
        if at.second != 0:
            problems.setdefault(d, []).append(f"seconds_not_zero#{i}")
            continue
        p = [_price(r.get(k)) for k in PRICE_KEYS]
        v = r.get(VOLUME_KEY)
        if None in p or isinstance(v, bool) or not isinstance(v, int) or v < 0:
            problems.setdefault(d, []).append(f"bad_values#{i}")
            continue
        o, h, lo, c = p
        if not (lo <= min(o, c) and h >= max(o, c) and lo <= h):
            problems.setdefault(d, []).append(f"ohlc_inconsistent#{i}")
            continue
        by.setdefault(d, []).append(Bar(at=at, hm=ts[8:12], open=o, high=h, low=lo, close=c, volume=v))
    for d in by:
        by[d].sort(key=lambda b: b.at)
    return by, problems, orphan


# ---------- 분류 ----------

def _agg(session: str, bars: list[Bar]) -> SessionAgg:
    return SessionAgg(session=session, open=bars[0].open, high=max(b.high for b in bars), low=min(b.low for b in bars),
                      close=bars[-1].close, volume=sum(b.volume for b in bars), bar_count=len(bars),
                      first_bar_at=bars[0].at, last_bar_at=bars[-1].at)


def complete_after(d: date) -> datetime:
    return datetime.combine(d, DAY_COMPLETE_AT, tzinfo=KST)


def classify_day(d: date, bars: list[Bar], asset_type: str | None, collected_at: datetime | None,
                 problems: list[str] | None = None, cal=None) -> DayResult:
    """종목 하나의 하루 분봉 -> 세션별 집계. 규칙에 맞지 않는 봉이 하나라도 있으면 그날 전체를 거부한다."""
    res = DayResult(day=d)
    cal = cal or krx_calendar.calendar()
    st = krx_calendar.is_trading_day(d, cal)
    if st is False:
        res.reject("non_trading_day")
    elif st is None:
        res.reject("calendar_unknown")
    elif krx_calendar.close_time(d, cal) != REGULAR_CLOSE:
        res.reject("special_session_schedule")
    if collected_at is None:
        res.reject("collected_at_unknown")
    elif collected_at < complete_after(d):
        res.reject("incomplete_day")
    for p in problems or []:
        res.reject(p.split("#")[0])
    seen: set[datetime] = set()
    for b in bars:
        if b.at in seen:
            res.reject("duplicate_timestamp")
        seen.add(b.at)
    if res.status == "rejected":
        return res

    groups: dict[str, list[Bar]] = {s: [] for s in SESSIONS}
    auction = [b for b in bars if b.hm == "1530"]
    late = [b for b in bars if "1531" <= b.hm <= "1539"]
    if auction:
        cand = auction[0] if auction[0].flat else None
        bad_late = late
    elif len(late) == 1 and late[0].flat:
        cand, bad_late = late[0], []
        res.flags.append("late_closing_auction")
    else:
        cand, bad_late = None, late
    if auction and cand is None:
        res.unclassified.append("1530")
    res.unclassified += [b.hm for b in bad_late]
    if cand is not None:
        groups[CLOSING_AUCTION].append(cand)
    after_ok = asset_type == "stock" and d >= AFTER_MARKET_START

    for b in bars:
        if b is cand or b.hm == "1530" or "1531" <= b.hm <= "1539":
            continue
        if "0900" <= b.hm <= "1520":
            groups[REGULAR].append(b)
        elif "1540" <= b.hm <= "1559" and cand is not None and b.flat and b.close == cand.close:
            groups[POST_CLOSE].append(b)
        elif "1600" <= b.hm <= "1959" and after_ok:
            groups[AFTER_MARKET].append(b)
        else:
            res.unclassified.append(b.hm)
    if res.unclassified:
        res.reject("unclassified_bars")
    if not groups[REGULAR]:
        res.reject("no_regular_bars")
    if res.status == "rejected":
        return res
    if cand is None:
        res.flags.append("no_closing_auction")
    res.sessions = {s: _agg(s, g) for s, g in groups.items() if g}
    return res
