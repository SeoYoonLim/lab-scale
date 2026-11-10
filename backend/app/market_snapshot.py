"""공개 시장 요약 GET /api/market/overview(2026-11-10, 41차)의 계산·조회.

왜 따로 두나: 시장 지수는 지금까지 일일 브리핑(로그인 + 오늘 브리핑 생성)의 market 영역과 리서치 답(app.market_overview)에만
있었다. 프런트 리서치 홈의 "오늘의 시장"은 브리핑이 없으면 비어 있었다. 이 모듈은 같은 저장 데이터로 로그인 없이 읽는 요약을 만든다.

규칙(모두 저장된 값으로만 계산하고 없는 값을 만들지 않는다):
- 지수: market_index(KS11 코스피, KQ11 코스닥)의 최근 두 행. 등락폭·등락률은 기준일 종가와 **직전 거래일(KRX 달력)** 종가로 계산한다.
  저장된 직전 행이 직전 거래일이 아니면(수집 공백) 여러 날에 걸친 변화를 "전일 대비"라고 부르지 않도록 null과 이유를 준다.
- 장중(거래일 15:30 전)에는 오늘 날짜 행을 쓰지 않는다(종가가 아니다). app.briefing.drop_open_session과 같은 기준이다.
- 상승·하락·보합: 같은 날 stock_price의 주식(ETF 제외) change_pct 부호. 수집 대상(시가총액 상위 종목)만이라 시장 전체 종목 수가 아니다.
  등락률이 비어 있는 종목은 상승·하락·보합 어디에도 넣지 않고 따로 센다(app.market_overview와 같은 정의).
- 신선도: 기대 거래일(app.krx_calendar.expected_trading_day) 대비 늦은 거래일 수로 latest(0)·delayed(1~5)·stale(6 이상)·no_data.
  일일 브리핑 freshness와 같은 기준(app.outlook.MAX_LAG_BUSINESS_DAYS)이라 화면의 지연 띠를 그대로 재사용할 수 있다.
- 거래대금·시장 전체 거래량은 저장하지 않는다(market_index에 거래량 열이 없다). 응답 unavailable에 적고 값을 만들지 않는다.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from sqlalchemy import text

from app import krx_calendar
from app.outlook import MAX_LAG_BUSINESS_DAYS

KST = krx_calendar.KST
INDEXES = (("KS11", "코스피"), ("KQ11", "코스닥"))
STATUS_LATEST, STATUS_DELAYED, STATUS_STALE, STATUS_NO_DATA = "latest", "delayed", "stale", "no_data"
OK, PARTIAL, NO_DATA = "ok", "partial", "no_data"
# company.market -> 요약 묶음. 코스닥 글로벌 세그먼트는 코스닥 시장에 속한다.
MARKET_GROUP = {"KOSPI": "KOSPI", "KOSDAQ": "KOSDAQ", "KOSDAQ GLOBAL": "KOSDAQ"}
UNAVAILABLE = ("trading_value", "market_volume")
SOURCE = {"provider": "FinanceDataReader", "tables": ["market_index", "stock_price"], "per_row_source_recorded": False}
NOTE = "저장된 일별 종가 기준이며 실시간 시세가 아닙니다. 상승·하락 종목 수는 수집 대상 종목만 센 값입니다."

REASON_NO_PREVIOUS = "no_previous_row"
REASON_GAP = "previous_trading_day_missing"


def cutoff_date(now: datetime) -> date:
    """쓸 수 있는 가장 늦은 행 날짜. 장중이면 오늘 행은 종가가 아니라 어제까지만 쓴다."""
    today = krx_calendar._kst(now).date()
    return today - timedelta(days=1) if krx_calendar.session_is_open(now) else today


def _round(v: float | None, n: int = 2) -> float | None:
    return None if v is None else round(v, n)


def index_item(code: str, name: str, rows: list[tuple], now: datetime) -> dict:
    """rows: 그 지수의 (price_date, close_price, created_at) 최신순 최대 2행(장중 행 제외 뒤). 행이 없으면 date=None."""
    base = {"code": code, "name": name, "date": None, "close": None, "previous_date": None, "previous_close": None,
            "change": None, "change_pct": None, "change_unavailable_reason": None, "first_collected_at": None,
            "lag_business_days": None}
    if not rows:
        return base
    d0, c0, created = rows[0]
    c0 = float(c0)
    out = {**base, "date": d0, "close": c0,
           "first_collected_at": created.astimezone(KST).isoformat() if created else None,
           "lag_business_days": krx_calendar.lag(d0, krx_calendar.expected_trading_day(now)).days}
    if len(rows) < 2:
        return {**out, "change_unavailable_reason": REASON_NO_PREVIOUS}
    d1, c1 = rows[1][0], float(rows[1][1])
    out.update(previous_date=d1, previous_close=c1)
    if d1 != krx_calendar.previous_trading_day(d0) or c1 <= 0:
        return {**out, "change_unavailable_reason": REASON_GAP}
    return {**out, "change": _round(c0 - c1), "change_pct": _round((c0 / c1 - 1) * 100)}


def _ratio(n: int, denom: int) -> float | None:
    return round(n / denom * 100, 1) if denom > 0 else None


def breadth_counts(up: int, down: int, flat: int, missing: int, registered: int) -> dict:
    """상승·하락·보합 집계. total = 그날 시세 행이 있는 종목 수(up+down+flat+change_missing),
    비율의 분모는 등락률이 있는 종목(up+down+flat). no_price는 등록된 주식 중 그날 행이 없는 종목."""
    priced = up + down + flat + missing
    known = up + down + flat
    return {"total": priced, "up": up, "down": down, "flat": flat, "change_missing": missing,
            "no_price": max(0, registered - priced), "registered": registered,
            "up_ratio_pct": _ratio(up, known), "down_ratio_pct": _ratio(down, known), "flat_ratio_pct": _ratio(flat, known)}


def breadth_item(day: date | None, groups: dict[str, tuple[int, int, int, int, int]], now: datetime) -> dict | None:
    """groups: 묶음(KOSPI/KOSDAQ/기타) -> (up, down, flat, change_missing, registered). day가 없으면 None."""
    if day is None:
        return None
    tot = [sum(g[i] for g in groups.values()) for i in range(5)]
    return {"date": day, "universe": "collected_stocks", "lag_business_days": krx_calendar.lag(day, krx_calendar.expected_trading_day(now)).days,
            **breadth_counts(*tot),
            "by_market": {k: breadth_counts(*v) for k, v in sorted(groups.items())}}


def freshness_status(lag: int | None) -> str:
    if lag is None:
        return STATUS_NO_DATA
    if lag == 0:
        return STATUS_LATEST
    return STATUS_DELAYED if lag <= MAX_LAG_BUSINESS_DAYS else STATUS_STALE


def build(index_rows: dict[str, list[tuple]], breadth_day: date | None, groups: dict, now: datetime,
          collection: dict | None = None) -> dict:
    """DB 없이 조립(테스트 대상). index_rows: 코드 -> 최신순 (date, close, created_at) 행(장중 행은 이미 뺀 것).
    collection: {"index": 마지막 시도 기록 | None, "prices": ...}(app.data_status.last_attempt). 지연·결측이 수집 실패 때문인지
    화면이 구분할 수 있게 그대로 싣는다. 이 값으로 숫자를 바꾸지는 않는다."""
    indices = [index_item(code, name, index_rows.get(code, []), now) for code, name in INDEXES]
    breadth = breadth_item(breadth_day, groups, now)
    missing = [i["code"] for i in indices if i["date"] is None] + ([] if breadth and breadth["total"] else ["breadth"])
    have = [i for i in indices if i["date"] is not None]
    status = NO_DATA if not have and "breadth" in missing else PARTIAL if missing else OK
    dates = [i["date"] for i in have]
    lags = [i["lag_business_days"] for i in have] + ([breadth["lag_business_days"]] if breadth and breadth["total"] else [])
    expected = krx_calendar.expected_trading_day(now)
    as_of = min(dates) if dates else None
    unknown = krx_calendar.lag(as_of, expected).unknown if as_of else 0
    worst = max(lags) if lags else None
    return {
        "as_of": as_of,
        "expected_date": expected,
        "generated_at": krx_calendar._kst(now).isoformat(),
        "status": status,
        "missing": missing,
        "freshness": {"status": freshness_status(worst), "lag_business_days": worst, "calendar_unknown_days": unknown,
                      "intraday_excluded": krx_calendar.session_is_open(now),
                      "dates_differ": len({*dates, *([breadth["date"]] if breadth and breadth["total"] else [])}) > 1},
        "indices": indices,
        "breadth": breadth if breadth and breadth["total"] else None,
        "last_collection": {"index": (collection or {}).get("index"), "prices": (collection or {}).get("prices")},
        "source": SOURCE,
        "unavailable": list(UNAVAILABLE),
        "note": NOTE,
    }


_INDEX_SQL = text(
    "SELECT index_code, price_date, close_price, created_at FROM ("
    " SELECT index_code, price_date, close_price, created_at,"
    "        row_number() OVER (PARTITION BY index_code ORDER BY price_date DESC) AS rn"
    " FROM market_index WHERE index_code IN ('KS11', 'KQ11') AND price_date <= :cutoff) t "
    "WHERE rn <= 2 ORDER BY index_code, price_date DESC"
)

# 그날(장중 행 제외한 최신 주식 시세 날짜) 묶음별 상승·하락·보합·등락률 결측과 등록 주식 수를 한 번에 센다.
_BREADTH_SQL = text(
    "WITH d AS (SELECT max(p.price_date) AS day FROM stock_price p JOIN company c ON c.id = p.company_id"
    "           WHERE c.asset_type = 'stock' AND p.price_date <= :cutoff),"
    " reg AS (SELECT coalesce(market, '') AS market, count(*) AS n FROM company WHERE asset_type = 'stock' GROUP BY 1),"
    " px AS (SELECT coalesce(c.market, '') AS market,"
    "               count(*) FILTER (WHERE p.change_pct > 0) AS up, count(*) FILTER (WHERE p.change_pct < 0) AS down,"
    "               count(*) FILTER (WHERE p.change_pct = 0) AS flat, count(*) FILTER (WHERE p.change_pct IS NULL) AS missing"
    "        FROM stock_price p JOIN company c ON c.id = p.company_id, d"
    "        WHERE c.asset_type = 'stock' AND p.price_date = d.day GROUP BY 1) "
    "SELECT (SELECT day FROM d), reg.market, coalesce(px.up, 0), coalesce(px.down, 0), coalesce(px.flat, 0),"
    "       coalesce(px.missing, 0), reg.n FROM reg LEFT JOIN px ON px.market = reg.market"
)


def load(db, now: datetime) -> dict:
    """DB 읽기 4회(지수 1, 상승·하락 1, 수집 기록 2)로 응답을 만든다. 쓰기 없음."""
    from app.data_status import last_attempt

    cutoff = cutoff_date(now)
    index_rows: dict[str, list[tuple]] = {}
    for code, d, close, created in db.execute(_INDEX_SQL, {"cutoff": cutoff}).all():
        if close is not None:
            index_rows.setdefault(code, []).append((d, close, created))
    groups: dict[str, list[int]] = {}
    day = None
    for d, market, up, down, flat, missing, n in db.execute(_BREADTH_SQL, {"cutoff": cutoff}).all():
        day = d
        key = MARKET_GROUP.get(market, "OTHER")
        g = groups.setdefault(key, [0, 0, 0, 0, 0])
        for i, v in enumerate((up, down, flat, missing, n)):
            g[i] += int(v)
    collection = {"index": last_attempt(db, "index"), "prices": last_attempt(db, "prices")}
    return build(index_rows, day, {k: tuple(v) for k, v in groups.items()}, now, collection)
