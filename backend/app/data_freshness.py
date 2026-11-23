"""데이터 종류별 '지금 있어야 할 최신 기준일'과 지연 판정(2026-11-23).

예전 판정(outlook.expected_trading_day)은 모든 데이터에 같은 날짜를 썼다: 거래일 장 마감(15:30) 직후부터 그날 데이터가 없으면 곧바로
"1영업일 지연"이었다. 그런데 실제 제공 시각은 데이터마다 다르다.

| 종류 | 그 거래일 D의 데이터가 정상적으로 있어야 하는 시각(KST) | 근거 |
|---|---|---|
| 주식(stock) | D 다음 거래일 개장 + 60분 | 공식 종가는 다음 거래일 기준가로 확정한다(app/official_close.py) |
| ETF(etf) | D 마감 + 5시간(보통 20:30) | 네이버 일별 시세는 마감 직후 나온다. 클라우드 수집 18~21시대 |
| 지수(index) | D 다음 날 08:00 | FDR KRX 지수 캐시의 마감 뒤 갱신이 21~23시, 늦으면 다음 날 06~07시(index_confirm) |
| 환율(fx) | D(평일) 다음 날 런던 0시 + 2시간 (겨울 11:00 / 여름 10:00 KST) | Yahoo KRW=X 일봉은 런던 하루 기준(collectors.fx_bar_complete) |

- 장 시작 전·장중·마감 뒤 유예 시간 안에는 직전 완료 거래일이 정상 최신이다(지연 아님).
- 휴장일·주말에는 마지막 거래일(환율은 마지막 평일)이 최신이면 정상이다.
- 지연 영업일 수: KRX 종류는 KRX 거래일, 환율은 평일 수로 센다. 종류끼리 서로 영향을 주지 않는다.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app import krx_calendar as K

KST = ZoneInfo("Asia/Seoul")
LONDON = ZoneInfo("Europe/London")
STOCK, ETF, INDEX, FX = "stock", "etf", "index", "fx"
KINDS = (STOCK, ETF, INDEX, FX)
STOCK_CONFIRM_AFTER_OPEN = timedelta(minutes=60)
ETF_AFTER_CLOSE = timedelta(hours=5)
INDEX_NEXT_DAY_AT = time(8, 0)
FX_AFTER_BAR_END = timedelta(hours=2)
_SCAN = 15


def _kst(now: datetime) -> datetime:
    return now.astimezone(KST) if now.tzinfo else now.replace(tzinfo=KST)


def due_at(kind: str, d: date) -> datetime:
    """거래일(환율은 평일) d의 데이터가 정상적으로 있어야 하는 시각(KST)."""
    if kind == STOCK:
        n = K.next_trading_day(d)
        return datetime.combine(n, K.open_time(n), tzinfo=KST) + STOCK_CONFIRM_AFTER_OPEN
    if kind == ETF:
        return datetime.combine(d, K.close_time(d), tzinfo=KST) + ETF_AFTER_CLOSE
    if kind == INDEX:
        return datetime.combine(d + timedelta(days=1), INDEX_NEXT_DAY_AT, tzinfo=KST)
    if kind == FX:
        return (datetime.combine(d + timedelta(days=1), time(0, 0), tzinfo=LONDON) + FX_AFTER_BAR_END).astimezone(KST)
    raise ValueError(kind)


def _candidates(kind: str, today: date):
    d = today
    for _ in range(_SCAN):
        if kind == FX:
            if d.weekday() < 5:
                yield d
            d -= timedelta(days=1)
        else:
            d = K.last_trading_day(d)
            yield d
            d -= timedelta(days=1)


def expected_date(kind: str, now: datetime) -> date:
    """지금 정상적으로 있어야 할 가장 최근 기준일."""
    now = _kst(now)
    for d in _candidates(kind, now.date()):
        if due_at(kind, d) <= now:
            return d
    raise ValueError("기준일을 찾지 못했다")


def pending_date(kind: str, now: datetime) -> date | None:
    """이미 끝났지만 아직 제공 유예 시간 안인 가장 최근 날(있으면 '수집 대기, 정상'으로 안내한다)."""
    now = _kst(now)
    exp = expected_date(kind, now)
    for d in _candidates(kind, now.date()):
        if d <= exp:
            return None
        finished = (datetime.combine(d, K.close_time(d), tzinfo=KST) if kind != FX
                    else datetime.combine(d + timedelta(days=1), time(0, 0), tzinfo=LONDON).astimezone(KST))
        if finished <= now:
            return d
    return None


def lag_days(kind: str, d: date | str | None, now: datetime) -> int | None:
    """d가 기대 기준일보다 몇 영업일 늦은가(0이면 정상). d가 없으면 None."""
    if d is None:
        return None
    d = date.fromisoformat(d) if isinstance(d, str) else d
    exp = expected_date(kind, now)
    if d >= exp:
        return 0
    if kind == FX:
        n, x = 0, d + timedelta(days=1)
        while x <= exp:
            n += x.weekday() < 5
            x += timedelta(days=1)
        return n
    return K.lag(d, exp).days


def status(kind: str, d: date | str | None, now: datetime) -> dict:
    exp = expected_date(kind, now)
    pend = pending_date(kind, now)
    lag = lag_days(kind, d, now)
    return {"kind": kind, "date": d.isoformat() if isinstance(d, date) else d, "expected_date": exp.isoformat(),
            "lag_business_days": lag, "delayed": bool(lag),
            "pending_date": pend.isoformat() if pend and (d is None or str(d) < pend.isoformat()) else None,
            "pending_due_at": due_at(kind, pend).isoformat() if pend and (d is None or str(d) < pend.isoformat()) else None}
