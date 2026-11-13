"""분봉으로 하루 시세를 세션별(정규장·장후 종가매매·애프터마켓)로 나누고 저장된 일봉과 대조한다(2026-11-13, 50차).

## 원천과 정의(50차 조사, docs/MARKET_DATA_QUALITY_AUDIT_50.md)
- 분봉: `api.stock.naver.com/chart/domestic/item/{t}/minute`(비공식). 분마다 시가·고가·저가·종가(currentPrice)·그 분 거래량.
  **최근 7거래일만** 준다(2026-11-13 확인: 11-05~11-13, 11-04 이전은 빈 목록).
  - KRX 체결만이다. 장중 합계가 실시간 응답의 KRX 거래량과 같고 NXT(`overMarketPriceInfo`) 거래량은 들어 있지 않다.
  - 가격·거래량은 원천의 **현재 수정주가 체계**다(분배락 전 ETF 분봉은 소수점이 붙은 조정값). FDR 일봉(fchart)과 같은 체계다.
  - 시각 구간: 09:00~15:30 정규장(15:30 봉 = 종가 단일가), 15:40~15:59 장후 종가매매(종가로만 체결), 16:00~19:59 KRX 애프터마켓.
- 완료된 날의 일봉(FDR = 네이버 fchart = m.stock 일별, 2026-09-14 이후 주식):
  시가 = 정규장 시가, 고가·저가 = 위 세 구간을 모두 포함한 KRX 하루 범위, 종가(원천) = 애프터마켓 마지막 체결,
  거래량 = KRX 하루 전체(분봉 합계 + 분봉에 없는 체결). NXT 체결은 들어 있지 않다.
- 저장 종가(stock_price.close_price)는 공식 종가(정규장)라 정규장 15:30 봉 종가와 같아야 한다.

## 이 모듈이 하지 않는 것
- DB에 쓰지 않는다(세션별 저장 테이블은 제안만, 문서 11절). 7거래일보다 오래된 날의 정규장 값은 만들 수 없다.
- 분봉에 없는 체결(거래량 차이, unattributed)의 구성은 확인하지 못했다(시간외 대량매매 등으로 추정, 미확인).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, time

from app import http_retry, krx_calendar

MINUTE_URL = "https://api.stock.naver.com/chart/domestic/item/{ticker}/minute"
HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://m.stock.naver.com/"}
REQUEST_TIMEOUT_S = 20
MINUTE_HISTORY_TRADING_DAYS = 7  # 원천이 주는 범위(관찰값). 이보다 오래된 날은 확인할 수 없다

REGULAR, CLOSING, AFTER = "regular", "closing_price", "after_market"
SESSIONS = {REGULAR: ("0900", "1530"), CLOSING: ("1531", "1559"), AFTER: ("1600", "2000")}
DAY_COMPLETE_AT = time(20, 10)  # 애프터마켓(~20:00) 뒤. 그 전의 오늘 분봉은 미완성이다
PRICE_TOLERANCE = 0.5  # 원 단위 가격(수정주가 분봉은 소수라 반올림 차이를 허용)
RELATIVE_TOLERANCE = 1e-4  # 수정주가 반올림(FDR 일봉은 정수, 분봉은 소수)


@dataclass(frozen=True)
class SessionBar:
    open: float
    high: float
    low: float
    close: float
    volume: int
    bars: int
    first: str  # HHMM
    last: str


def _hhmm(b: dict) -> str:
    return str(b["localDateTime"])[8:12]


def _day(b: dict) -> date:
    s = str(b["localDateTime"])
    return date(int(s[:4]), int(s[4:6]), int(s[6:8]))


def aggregate(bars: list[dict]) -> dict[date, dict[str, SessionBar]]:
    """분봉 목록 -> {날짜: {세션: SessionBar}}. 봉이 없는 세션은 빠진다."""
    by: dict[date, list[dict]] = {}
    for b in sorted(bars, key=lambda x: str(x["localDateTime"])):
        by.setdefault(_day(b), []).append(b)
    out: dict[date, dict[str, SessionBar]] = {}
    for d, rows in by.items():
        out[d] = {}
        for name, (lo, hi) in SESSIONS.items():
            seg = [b for b in rows if lo <= _hhmm(b) <= hi]
            if seg:
                out[d][name] = SessionBar(
                    open=float(seg[0]["openPrice"]), high=max(float(b["highPrice"]) for b in seg),
                    low=min(float(b["lowPrice"]) for b in seg), close=float(seg[-1]["currentPrice"]),
                    volume=int(sum(int(b["accumulatedTradingVolume"]) for b in seg)), bars=len(seg),
                    first=_hhmm(seg[0]), last=_hhmm(seg[-1]))
    return out


def day_complete(d: date, now: datetime) -> bool:
    """그날 분봉이 끝났는가(지난 날이거나 오늘 20:10 뒤)."""
    now = krx_calendar._kst(now)
    return d < now.date() or (d == now.date() and now.time() >= DAY_COMPLETE_AT)


def regular_issue(sessions: dict[str, SessionBar], d: date, now: datetime) -> str | None:
    """정규장 값을 쓸 수 없는 이유. 쓸 수 있으면 None."""
    if not day_complete(d, now):
        return "day_incomplete"
    reg = sessions.get(REGULAR)
    if reg is None:
        return "no_regular_bars"
    if reg.last != "1530":
        return "no_close_auction_bar"  # 15:30 봉(종가 단일가)이 없으면 정규장 종가를 정할 수 없다(50차: 주식 35/1,800행)
    return None


def _same(a: float, b: float) -> bool:
    return abs(a - b) <= max(PRICE_TOLERANCE, abs(b) * RELATIVE_TOLERANCE)


def compare_day(stored: dict, sessions: dict[str, SessionBar], d: date, now: datetime) -> dict:
    """저장 행(open/high/low/close/volume)과 그날 세션 집계의 대조. 값을 만들지 않고 판정만 한다."""
    issue = regular_issue(sessions, d, now)
    reg, cls, aft = sessions.get(REGULAR), sessions.get(CLOSING), sessions.get(AFTER)
    minute_total = sum(s.volume for s in sessions.values())
    out = {"date": d.isoformat(), "issue": issue,
           "regular": asdict(reg) if reg else None, "closing_price": asdict(cls) if cls else None,
           "after_market": asdict(aft) if aft else None}
    if issue or reg is None:
        return out

    def source(stored_v: float, pick) -> str:
        if _same(stored_v, pick(reg)):
            return REGULAR
        for name, s in ((AFTER, aft), (CLOSING, cls)):
            if s is not None and _same(stored_v, pick(s)):
                return name
        return "unknown"

    vol = stored.get("volume")
    out.update({
        "open_is_regular": _same(stored["open"], reg.open),
        "close_is_regular_close": _same(stored["close"], reg.close),
        "high_from": source(stored["high"], lambda s: s.high),
        "low_from": source(stored["low"], lambda s: s.low),
        "volume_regular": reg.volume,
        "volume_closing_price": cls.volume if cls else 0,
        "volume_after_market": aft.volume if aft else 0,
        "volume_unattributed": None if vol is None else int(vol) - minute_total,
        "volume_over_regular_pct": None if vol is None or not reg.volume else round((int(vol) / reg.volume - 1) * 100, 2),
    })
    return out


def fetch_minute(ticker: str, start: date, end: date, get=None) -> list[dict]:
    """start~end(포함) 분봉. 원천은 최근 7거래일만 준다(그보다 앞 날짜는 빈 목록)."""
    get = get or (lambda url, params: http_retry.get(url, params=params, headers=HEADERS, timeout=REQUEST_TIMEOUT_S,
                                                       label="naver minute").json())
    rows = get(MINUTE_URL.format(ticker=ticker),
               {"startDateTime": start.strftime("%Y%m%d") + "0000", "endDateTime": end.strftime("%Y%m%d") + "2359"})
    if not isinstance(rows, list):
        raise ValueError("unexpected naver minute payload")
    return rows
