"""국내 주식 공식 종가(KRX 정규장 종가) 복원과 원천 종가와의 차이 분류(2026-11-11, 44차).

## 왜 필요한가
- FinanceDataReader의 국내 주식 기본 소스는 네이버 일봉(`fchart.stock.naver.com/sise.nhn`)이다.
- KRX 애프터마켓(16:00~20:00, ETF·ETN 제외)이 열린 2026-09-14부터 네이버·다음 일봉의 "종가"는 애프터마켓 종료 시점의
  마지막 체결가다. 한국거래소는 "종가는 15:30경 정규시장 종료 시 형성되는 최종가격"이고 포털이 애프터마켓 종료 가격을
  종가로 표시한다고 안내했다(경북매일 2026-09-17 보도). 거래소 업무규정 원문은 확인하지 못했다.
- 다음 거래일의 기준가(= 전일 정규장 종가)는 네이버 일별 시세의 `closePrice - compareToPreviousClosePrice`로 복원된다.
  42·43차 실데이터 대조: 2025-02-21~2026-09-11 주식 2종목 불일치 0일, 2026-09-14 이후 32·37일 불일치.

## 이 모듈이 하지 않는 것
- 공식 원천(KRX 정보데이터마켓플레이스, 공공데이터포털 금융위원회_주식시세정보)은 인증키·라이선스가 필요해 쓰지 않는다.
- 기준가가 조정되는 날(분할·병합·권리락·ETF 분배락)은 복원값이 그날 종가가 아니다. 그날은 원천 종가를 그대로 둔다.
- 최신 거래일은 다음 거래일 기준가가 아직 없어 복원할 수 없다(provisional).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from app import http_retry

NAVER_DAILY_URL = "https://m.stock.naver.com/api/stock/{ticker}/price"
PAGE_SIZE = 60  # 이보다 크면 400(2026-11-11 확인)
MAX_PAGES = 10
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


def fetch_daily(ticker: str, since: date, get=None) -> list[dict]:
    """네이버 일별 시세(최신부터)를 since까지 페이지로 읽는다. 비공식 API라 형식이 바뀌면 KeyError/ValueError가 난다."""
    get = get or (lambda url, params: http_retry.get(url, params=params, headers=HEADERS, timeout=REQUEST_TIMEOUT_S,
                                                       label="naver daily price").json())
    rows: list[dict] = []
    for page in range(1, MAX_PAGES + 1):
        batch = get(NAVER_DAILY_URL.format(ticker=ticker), {"pageSize": PAGE_SIZE, "page": page})
        if not isinstance(batch, list):
            raise ValueError("unexpected naver daily price payload")
        rows += batch
        if len(batch) < PAGE_SIZE or (batch and batch[-1]["localTradedAt"][:10] < since.isoformat()):
            break
    # since 하루 전 거래일 1행은 남긴다(since 날의 공식 종가는 다음 날로 복원하지만, 전일 종가가 등락률 계산에 필요)
    keep = sorted({r["localTradedAt"][:10]: r for r in rows}.values(), key=lambda r: r["localTradedAt"])
    first = next((i for i, r in enumerate(keep) if r["localTradedAt"][:10] >= since.isoformat()), len(keep))
    return keep[max(0, first - 1):]


def official_records(ticker: str, asset_type: str, start: date, rows: list[dict] | None = None) -> tuple[list[dict], dict]:
    """공식 종가 기준의 stock_price 레코드(start 이후)와 분류 통계. rows를 주면 네트워크를 쓰지 않는다(테스트).

    - 종가: 주식은 복원한 공식 종가, ETF는 네이버 일별 시세의 실제 종가(FDR 일봉은 분배락 뒤 과거 전체를 조정한 수정주가다).
    - 시가·고가·저가·거래량: 같은 네이버 일별 시세 행(완료된 날은 정규장 값, 2026-11-11 fchart와 같음을 확인).
    - 주식의 최신 거래일은 쓰지 않는다: 다음 거래일 기준가가 아직 없고, 당일 행은 NXT까지 합친 통합 값이다.
    - 등락률은 저장하는 공식 종가끼리 다시 계산한다(원천의 전일 대비를 쓰지 않는다).
    """
    rows = rows if rows is not None else fetch_daily(ticker, start)
    by_day = {date.fromisoformat(r["localTradedAt"][:10]): r for r in rows}
    out: list[dict] = []
    stats: dict[str, int] = {}
    prev = None
    for d in classify(rows, asset_type):
        close = d.official_close
        if d.kind == PROVISIONAL and asset_type == "etf":
            close = d.source_close  # ETF는 애프터마켓 대상이 아니다. 최신일 거래량은 장 마감 뒤 조금 늘 수 있다
        if close is None:
            stats["skipped_provisional"] = stats.get("skipped_provisional", 0) + 1
            continue
        stats[d.kind] = stats.get(d.kind, 0) + 1
        r = by_day[d.day]
        if d.day >= start:
            out.append({
                "price_date": d.day,
                "open_price": _num(r["openPrice"]),
                "high_price": _num(r["highPrice"]),
                "low_price": _num(r["lowPrice"]),
                "close_price": close,
                "volume": int(_num(r["accumulatedTradingVolume"])),
                "change_pct": round((close / prev - 1) * 100, 2) if prev else None,
            })
        prev = close
    return out, stats
