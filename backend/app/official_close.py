"""국내 주식 공식 종가(KRX 정규장 종가) 복원과 원천 종가와의 차이 분류(2026-11-11, 44차).

## 왜 필요한가
- FinanceDataReader의 국내 주식 기본 소스는 네이버 일봉(`fchart.stock.naver.com/sise.nhn`)이다.
- KRX 애프터마켓(16:00~20:00, ETF·ETN 제외)이 열린 2026-09-14부터 네이버·다음 일봉의 "종가"는 애프터마켓 종료 시점의
  마지막 체결가다. 한국거래소는 "종가는 15:30경 정규시장 종료 시 형성되는 최종가격"이고 포털이 애프터마켓 종료 가격을
  종가로 표시한다고 안내했다(경북매일 2026-09-17 보도). 거래소 업무규정 원문은 확인하지 못했다.
- 다음 거래일의 기준가(= 전일 정규장 종가)는 네이버 일별 시세의 `closePrice - compareToPreviousClosePrice`로 복원된다.
  42·43차 실데이터 대조: 2025-02-21~2026-09-11 주식 2종목 불일치 0일, 2026-09-14 이후 32·37일 불일치.

## 45차(2026-11-11) 저장 기준: 공식 종가를 원천의 수정주가 체계로
- 저장 종가 = 공식 종가 x (원천 FDR 종가 / 네이버 실제 종가). FDR은 기업 이벤트(무상증자·분할)와 ETF 분배 뒤 과거 전체를
  조정한다. 실제 종가로 덮어쓰면 교정 구간 경계에 가짜 급등락이 생기고(엘앤씨바이오 2026-09-14 +93%), 저장 전 검증이 일부를
  격리해 기준이 섞인다(커버드콜 ETF 38~58% 차이). 그래서 조정 체계는 원천을 따르고, 애프터마켓 차이만 고친다.
- ETF는 애프터마켓 대상이 아니라 공식 종가 = 실제 종가이므로 결과는 원천(FDR) 값 그대로다(44차의 "ETF = 실제 종가"는 바꿨다).
- 한계: 조정 계수는 FDR 것을 쓴다(기업 이벤트일에 KRX 기준가 계수와 조금 다를 수 있다. 엘앤씨바이오 10-26 -17.69% vs 기준가 -18.08%).

## 이 모듈이 하지 않는 것
- 공식 원천(KRX 정보데이터마켓플레이스, 공공데이터포털 금융위원회_주식시세정보)은 인증키·라이선스가 필요해 쓰지 않는다.
- 기준가가 조정되는 날(분할·병합·권리락·ETF 분배락)은 복원값이 그날 종가가 아니다. 그날은 원천 종가를 그대로 둔다.
- 최신 거래일은 다음 거래일 기준가가 아직 없어 복원할 수 없다(provisional).
- 차이가 애프터마켓 제한폭 안이어도 원천 조정 비율이 다음 날 바뀌면(기업 이벤트) 교정하지 않는다(46차, event_boundary).
- 시가·고가·저가·거래량은 원천 값이다. 2026-09-14 이후 주식은 애프터마켓 체결이 포함된 통합 값이다(46차 확인: 펩트론 2026-10-07
  저가 91,500은 애프터마켓 체결, 정규장 종가는 130,600). 종가만 정규장 기준으로 바꾼다.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time

from app import http_retry

NAVER_DAILY_URL = "https://m.stock.naver.com/api/stock/{ticker}/price"
PAGE_SIZE = 60  # 이보다 크면 400(2026-11-11 확인)
MAX_PAGES = 40  # 2,400거래일(약 10년). 45차: 10이면 600행에서 멈춰 ETF 전체 기간(약 750행)의 앞부분이 수정주가로 남았다
REGULAR_CLOSE = time(15, 30)  # 이 시각 전 당일 행은 장중 값이다
REQUEST_TIMEOUT_S = 10
HEADERS = {"User-Agent": "Mozilla/5.0"}

AFTER_MARKET_START = date(2026, 9, 14)  # KRX 애프터마켓 개장일(한국경제 2026-09-14 보도)
# 애프터마켓 가격제한폭은 기준가 ±30%(보도). 이를 넘는 차이는 애프터마켓으로 설명할 수 없어 기준가 조정으로 본다.
AFTER_MARKET_MAX_GAP = 0.30

SAME = "same"
AFTER_MARKET = "after_market"
DISTRIBUTION = "distribution"  # ETF 분배락 등 기준가 조정(ETF는 애프터마켓 대상이 아니다)
BASE_ADJUSTED = "base_adjusted"  # 주식: 애프터마켓 개장 전이거나 차이가 제한폭 밖(분할·권리락 등)
PROVISIONAL = "provisional"  # 최신 거래일: 다음 거래일 기준가가 없어 확정 불가
# 46차: 차이가 제한폭 안이라 애프터마켓으로 보였지만 원천(FDR)의 조정 비율이 다음 날 바뀐 날(그 사이에 기준가 조정 = 기업 이벤트).
# 다음 날 기준가가 조정된 값이라 공식 종가로 쓸 수 없다. 교정하지 않고 원천 값을 둔다(official_records에서만 정해진다).
EVENT_BOUNDARY = "event_boundary"
RATIO_TOLERANCE_WON = 1.5  # FDR 수정주가는 원 단위 반올림이다. 두 날의 조정 비율 차이 x 종가가 이보다 크면 비율이 바뀐 것으로 본다


@dataclass(frozen=True)
class DayClose:
    day: date
    source_close: float  # 원천(네이버 일봉) 종가
    official_close: float | None  # 공식 종가(복원). 확정 불가면 None
    kind: str


def _num(v) -> float:
    return float(str(v).replace(",", ""))


def classify(rows: list[dict], asset_type: str) -> list[DayClose]:
    """rows: 네이버 일별 시세 항목(localTradedAt, closePrice, compareToPreviousClosePrice). 날짜 오름차순으로 정렬해 쓴다.
    그날의 공식 종가 = 다음 거래일 closePrice - 다음 거래일 compareToPreviousClosePrice(부호 있는 값)."""
    rs = sorted(rows, key=lambda r: r["localTradedAt"])
    out = []
    for i, r in enumerate(rs):
        d = date.fromisoformat(r["localTradedAt"][:10])
        src = _num(r["closePrice"])
        if i + 1 >= len(rs):
            out.append(DayClose(d, src, None, PROVISIONAL))
            continue
        nxt = rs[i + 1]
        base = _num(nxt["closePrice"]) - _num(nxt["compareToPreviousClosePrice"])
        if abs(base - src) < 0.5:
            out.append(DayClose(d, src, src, SAME))
        elif asset_type == "etf":
            out.append(DayClose(d, src, src, DISTRIBUTION))  # ETF는 원천 종가가 정규장 종가다
        elif d >= AFTER_MARKET_START and abs(src - base) / base <= AFTER_MARKET_MAX_GAP:
            out.append(DayClose(d, src, base, AFTER_MARKET))
        else:
            out.append(DayClose(d, src, src, BASE_ADJUSTED))
    return out


def mark_event_boundaries(days: list[DayClose], source_close: dict[date, float]) -> list[DayClose]:
    """46차: 애프터마켓으로 분류된 날 중 원천(FDR) 조정 비율(FDR 종가 / 네이버 실제 종가)이 다음 날과 다른 날을 EVENT_BOUNDARY로 바꾼다.

    비율이 바뀌었다 = 두 날 사이에 기준가 조정(무상증자·권리락 등)이 있었다는 뜻이다. 다음 날 기준가는 조정된 값이라 그날의 공식 종가가
    아니다. 예: 삼성바이오로직스 2026-10-01(비율 0.9923 -> 다음 날 1.0, 실제 종가와 기준가 차이 0.7%). 조정된 기준가에 비율을 한 번 더
    곱하면 약 -0.77% 틀린 값이 된다. 확인할 수 없으므로 교정하지 않고 원천 종가를 둔다(official_close = source_close).
    - 마지막 행의 비율은 1로 본다: 마지막 거래일 뒤의 조정은 없고, 장중 행이면 두 원천의 조회 시각 차이로 비율이 흔들린다.
    - 다음 날이 원천에 없으면 확인할 수 없으므로 같은 처리를 한다.
    """
    def ratio(i: int) -> float | None:
        if i == len(days) - 1:
            return 1.0
        s = source_close.get(days[i].day)
        return s / days[i].source_close if s is not None and days[i].source_close else None

    out = list(days)
    for i, d in enumerate(days):
        if d.kind != AFTER_MARKET:
            continue
        r_day, r_next = ratio(i), ratio(i + 1)
        if r_day is not None and (r_next is None or abs(r_day - r_next) * d.source_close > RATIO_TOLERANCE_WON):
            out[i] = DayClose(d.day, d.source_close, d.source_close, EVENT_BOUNDARY)
    return out


def fetch_daily(ticker: str, since: date, get=None) -> list[dict]:
    """네이버 일별 시세(최신부터)를 since까지 페이지로 읽는다. 비공식 API라 형식이 바뀌면 KeyError/ValueError가 난다."""
    get = get or (lambda url, params: http_retry.get(url, params=params, headers=HEADERS, timeout=REQUEST_TIMEOUT_S,
                                                       label="naver daily price").json())
    rows: list[dict] = []
    reached = False
    for page in range(1, MAX_PAGES + 1):
        batch = get(NAVER_DAILY_URL.format(ticker=ticker), {"pageSize": PAGE_SIZE, "page": page})
        if not isinstance(batch, list):
            raise ValueError("unexpected naver daily price payload")
        rows += batch
        if len(batch) < PAGE_SIZE or (batch and batch[-1]["localTradedAt"][:10] < since.isoformat()):
            reached = True  # 원천의 끝(상장일)까지 왔거나 since 앞까지 읽었다
            break
    if not reached:
        # 45차: 페이지 한도에서 멈추면 since까지 못 읽은 채 조용히 끝났다. 앞부분이 옛 값으로 남지 않게 실패로 알린다
        raise ValueError(f"naver daily price did not reach {since.isoformat()} within {MAX_PAGES} pages")
    # since 하루 전 거래일 1행은 남긴다(since 날의 공식 종가는 다음 날로 복원하지만, 전일 종가가 등락률 계산에 필요)
    keep = sorted({r["localTradedAt"][:10]: r for r in rows}.values(), key=lambda r: r["localTradedAt"])
    first = next((i for i, r in enumerate(keep) if r["localTradedAt"][:10] >= since.isoformat()), len(keep))
    return keep[max(0, first - 1):]


def official_records(ticker: str, asset_type: str, start: date, rows: list[dict] | None = None,
                     now: datetime | None = None, source: dict | None = None) -> tuple[list[dict], dict]:
    """공식 종가 기준의 stock_price 레코드(start 이후)와 분류 통계. rows를 주면 네트워크를 쓰지 않는다(테스트).

    - 종가: 주식은 복원한 공식 종가, ETF는 네이버 일별 시세의 실제 종가(FDR 일봉은 분배락 뒤 과거 전체를 조정한 수정주가다).
    - 시가·고가·저가·거래량: 같은 네이버 일별 시세 행(fchart와 같다). 2026-09-14 이후 주식은 애프터마켓 체결이 포함된 통합 값이다(46차).
    - 주식의 최신 거래일은 쓰지 않는다: 다음 거래일 기준가가 아직 없고, 당일 행은 NXT까지 합친 통합 값이다.
    - 등락률은 저장하는 공식 종가끼리 다시 계산한다(원천의 전일 대비를 쓰지 않는다).
    - ETF의 최신 거래일은 그날 15:30(KST) 뒤에만 쓴다(now는 KST 시각, 없으면 쓴다). 장중에는 그 행이 장중 값이다(45차).
    - source(45차, 권장): {날짜: 원천(FDR) 레코드}. 주면 원천 행(수정주가 체계의 시가·고가·저가·거래량)을 그대로 쓰고 종가만
      공식 종가 x (원천 종가 / 네이버 실제 종가)로 바꾼다. FDR은 기업 이벤트·ETF 분배 뒤 과거 전체를 조정하므로, 실제 종가를 그대로
      쓰면 교정 구간 경계에 가짜 급등락이 생긴다(엘앤씨바이오 2026-09-14 +93%). ETF는 공식 종가 = 실제 종가라 원천 값 그대로다.
      원천에 없는 날은 쓰지 않는다(no_source). stats["after_market_dates"]는 애프터마켓 차이로 바뀐 날(저장 전 충돌 검사 제외용).
      46차: 원천 조정 비율이 다음 날 바뀐 애프터마켓 분류일은 교정하지 않는다(mark_event_boundaries, stats["event_boundary"],
      날짜는 stats["event_boundary_dates"]).
    """
    rows = rows if rows is not None else fetch_daily(ticker, start)
    by_day = {date.fromisoformat(r["localTradedAt"][:10]): r for r in rows}
    out: list[dict] = []
    stats: dict = {"after_market_dates": [], "event_boundary_dates": []}
    prev = None
    days = classify(rows, asset_type)
    if source is not None:
        days = mark_event_boundaries(days, {k: float(v["close_price"]) for k, v in source.items()})
    for d in days:
        close = d.official_close
        if d.kind == PROVISIONAL and asset_type == "etf":
            intraday = now is not None and d.day >= now.date() and now.time() < REGULAR_CLOSE
            # ETF는 애프터마켓 대상이 아니다. 최신일 거래량은 장 마감 뒤 조금 늘 수 있다. 장중이면 쓰지 않는다
            close = None if intraday else d.source_close
        if close is None:
            stats["skipped_provisional"] = stats.get("skipped_provisional", 0) + 1
            continue
        if source is not None:
            src = source.get(d.day)
            if src is None:
                stats["no_source"] = stats.get("no_source", 0) + 1
                prev = None  # 끊긴 날 다음의 등락률은 계산하지 않는다(섞인 기준끼리 비교하지 않게)
                continue
            close = round(close * (float(src["close_price"]) / d.source_close), 2)
        stats[d.kind] = stats.get(d.kind, 0) + 1
        if d.kind == AFTER_MARKET:
            stats["after_market_dates"].append(d.day.isoformat())
        if d.kind == EVENT_BOUNDARY and d.day >= start:
            stats["event_boundary_dates"].append(d.day.isoformat())  # 46차 승인: 교정하지 않은 행을 따로 남긴다
        r = by_day[d.day]
        if d.day >= start:
            base = dict(src) if source is not None else {
                "price_date": d.day,
                "open_price": _num(r["openPrice"]),
                "high_price": _num(r["highPrice"]),
                "low_price": _num(r["lowPrice"]),
                "volume": int(_num(r["accumulatedTradingVolume"])),
            }
            out.append({**base, "price_date": d.day, "close_price": close,
                        "change_pct": round((close / prev - 1) * 100, 2) if prev else None})
        prev = close
    return out, stats
