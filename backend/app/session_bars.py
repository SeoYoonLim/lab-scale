"""분봉을 KRX 거래 세션으로 분류하고 세션별로 집계한다(53차, 54차에 거래일별 일정 기반으로 변경). 순수 함수, DB·네트워크 없음.

근거: docs/SESSION_MARKET_DATA_IMPLEMENTATION_REHEARSAL_53.md 3절(보존 분봉 1,192,628개 전수 조사), 일정:
app/session_schedule.py + app/data/krx_session_schedule.json(54차, docs/SESSION_MARKET_DATA_PIPELINE_54.md).
- 원천: 네이버 국내 분봉(app.session_ohlcv.MINUTE_URL), KRX 체결만. `localDateTime`은 KST **봉 시작 시각**.
- 세션(시각은 그날 일정에서 읽는다. 정규 일정 값은 괄호):
  regular          정규 연속매매 창(09:00~15:20). 시가 단일가 체결은 첫 봉에 섞여 나눌 수 없다
  closing_auction  종가 단일가 봉(15:30) 하나. 그 봉이 없을 때만, 일정에 지연 창(15:31~15:39)이 있으면 그 안의 봉 하나.
                   어느 경우든 시가=고가=저가=종가여야 하고, 지연 창 봉이 둘 이상이면 고르지 않고 거부한다
  post_close       장후 시간외 종가매매 창(15:40~15:59). 모든 봉이 시가=고가=저가=종가=그날 종가 단일가 가격
  after_market     KRX 애프터마켓 창(16:00~19:59). 일정이 정한 자산(주식)과 시작일(2026-09-14) 이후만
- 위에 맞지 않는 봉이 하나라도 있으면 그 종목-날짜 전체를 거부한다(일부 세션만 저장하면 하루가 완전한 것처럼 보이기 때문).
  잘못된 봉을 빼고 나머지를 집계하지 않는다.
- 공식 종가와 같은지는 분류에 쓰지 않는다(분류 뒤 대조만 한다). NXT 세션은 원천에 없어 만들지 않는다.
- 일정을 알 수 없는 날(휴장일, 달력 범위 밖, 달력에 특수 마감이 있는데 일정이 등록되지 않은 날)은 거부한다.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation

from app import krx_calendar, session_schedule

RULES_VERSION = 1
VENUE_KRX = "KRX"
REGULAR, CLOSING_AUCTION, POST_CLOSE, AFTER_MARKET = "regular", "closing_auction", "post_close", "after_market"
SESSIONS = (REGULAR, CLOSING_AUCTION, POST_CLOSE, AFTER_MARKET)
KST = krx_calendar.KST
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
    flags: list[str] = field(default_factory=list)  # late_closing_auction | no_closing_auction | calendar_close_mismatch
    schedule: dict | None = None  # 분류에 쓴 일정(session_schedule.DaySchedule.describe)

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
    for d, bars in by.items():  # 원본이 시간 순서가 아니면(같은 시각 포함) 그날을 거부한다. 정렬해서 감추지 않는다
        if any(x.at >= y.at for x, y in zip(bars, bars[1:])):
            problems.setdefault(d, []).append("out_of_order")
        bars.sort(key=lambda b: b.at)
    return by, problems, orphan


# ---------- 분류 ----------

def _agg(session: str, bars: list[Bar]) -> SessionAgg:
    return SessionAgg(session=session, open=bars[0].open, high=max(b.high for b in bars), low=min(b.low for b in bars),
                      close=bars[-1].close, volume=sum(b.volume for b in bars), bar_count=len(bars),
                      first_bar_at=bars[0].at, last_bar_at=bars[-1].at)


def complete_after(d: date, sched: session_schedule.DaySchedule | None = None) -> datetime:
    """그날 분봉이 모두 끝난 시각(일정의 마지막 봉 + 11분). 정규 일정은 20:10 KST."""
    if sched is None:
        sched, _ = session_schedule.resolve(d)
    if sched is None:
        return datetime.combine(d, time(20, 10), tzinfo=KST)
    return sched.complete_after()


def classify_day(d: date, bars: list[Bar], asset_type: str | None, collected_at: datetime | None,
                 problems: list[str] | None = None, cal=None, sched_data: session_schedule.Schedule | None = None) -> DayResult:
    """종목 하나의 하루 분봉 -> 세션별 집계. 규칙에 맞지 않는 봉이 하나라도 있으면 그날 전체를 거부한다."""
    res = DayResult(day=d)
    cal = cal or krx_calendar.calendar()
    ds, why = session_schedule.resolve(d, cal, sched_data)
    if ds is None:
        res.reject(why)
    else:
        res.schedule = ds.describe()
        if ds.regular_close != krx_calendar.close_time(d, cal):
            res.flags.append("calendar_close_mismatch")  # 분류는 세션 일정으로 한다(달력 갱신 필요, 보고서 16절)
    if collected_at is None:
        res.reject("collected_at_unknown")
    elif ds is not None and collected_at < ds.complete_after():
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
    auction = [b for b in bars if b.hm == ds.closing_at]
    late = [b for b in bars if ds.late_window is not None and b.hm in ds.late_window]
    if auction:
        cand = auction[0] if auction[0].flat else None
        if cand is None:
            res.unclassified.append(ds.closing_at)
        res.unclassified += [b.hm for b in late]  # 종가 단일가 봉이 있는 날의 지연 창 봉은 설명할 수 없다
    elif len(late) == 1 and late[0].flat:
        cand = late[0]
        res.flags.append("late_closing_auction")
    else:
        cand = None
        res.unclassified += [b.hm for b in late]  # 후보가 여럿이거나 단일가가 아니면 고르지 않는다
    if cand is not None:
        groups[CLOSING_AUCTION].append(cand)
    after = ds.after_market_for(asset_type)
    used = {id(b) for b in auction} | {id(b) for b in late}

    for b in bars:
        if id(b) in used:
            continue
        if b.hm in ds.regular:
            groups[REGULAR].append(b)
        elif ds.post_close is not None and b.hm in ds.post_close and cand is not None and b.flat and b.close == cand.close:
            groups[POST_CLOSE].append(b)
        elif after is not None and b.hm in after:
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
