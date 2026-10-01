"""
네이버 금융(finance.naver.com) 종목 페이지가 장중에 쓰는 비공식 실시간 시세 폴링 API 호출 + 캐싱.

## 조사 결과 (2026-10-01, 실제 호출로 확인)

- URL: `https://polling.finance.naver.com/api/realtime/domestic/stock/{ticker}`
  finance.naver.com 종목 페이지가 `pollingInterval`(응답에 포함, 보통 7000ms)마다 그대로 폴링하는 엔드포인트다.
  계좌/인증/쿠키 없이 호출 가능하고(브라우저가 보내는 Referer/User-Agent 없이도 200 응답 확인), 짧은 시간
  연속 10회 호출에도 차단 응답은 없었다.
- 응답은 `{"datas": [...]}` 형태이고, 없는 종목코드도 404가 아니라 200 + `"datas": []`로 온다. 즉 상태 코드만으로는
  실패를 판별할 수 없어 `datas`가 비어 있는지 반드시 확인해야 한다.
- 금액류 필드(`closePrice` 등)는 "268,000"처럼 천단위 콤마가 섞인 문자열이지만, 같은 값의 콤마 없는 원본이
  `...Raw` 필드(`closePriceRaw`, `compareToPreviousClosePriceRaw`, `fluctuationsRatioRaw`)로 함께 오므로 그걸 쓴다.
  전일대비/등락률은 하락이면 이미 음수로 온다(부호 별도 계산 불필요).
- `marketStatus`가 "OPEN"이 아니면(장 마감 후/주말/공휴일 등) 마지막 체결가만 반복해서 내려주고 더는 "실시간"이
  아니므로, 이 모듈은 `marketStatus == "OPEN"`일 때만 성공으로 보고 그 외는 전부 폴백 대상으로 취급한다.

## 설계 전제

비공식 API이므로 언제든 막히거나(차단/변경) 응답 형식이 바뀔 수 있다. 그래서:
- 네트워크 오류, 타임아웃, JSON 파싱 실패, 필드 누락, 장 시간 외 등 어떤 경우에도 예외를 던지지 않고 None을
  돌려주며, 호출측(`get_realtime_price_data`)이 DB에 저장된 최근 종가(FinanceDataReader 수집분)로 폴백한다.
- 종목별로 `CACHE_TTL_SECONDS` 동안은 캐시된 값을 그대로 돌려줘서, 프론트가 짧은 주기로 반복 호출해도
  상위 소스(네이버)에는 그 주기로 요청이 가지 않게 한다. 폴백 결과도 캐시한다 - 그래야 장 마감 후처럼
  네이버 호출이 매번 폴백으로 끝나는 상황에서도 매 요청마다 네이버를 다시 두드리지 않는다.
"""

import time

import requests

from app.models import Company, StockPrice

NAVER_REALTIME_URL = "https://polling.finance.naver.com/api/realtime/domestic/stock/{ticker}"
# 상위 소스 장애 시 응답이 오래 걸리지 않도록 짧게 끊는다.
REQUEST_TIMEOUT_SECONDS = 3
# 요구사항: 종목별 3~5초 캐싱.
CACHE_TTL_SECONDS = 4.0

# ticker -> (캐싱된 시각(time.monotonic()), 응답 데이터). 프로세스 메모리 캐시라 여러 워커로 띄우면
# 워커별로 따로 캐싱된다(지금 규모에서는 문제 없음; 필요해지면 redis 등 공유 캐시로 옮긴다).
_cache: dict[str, tuple[float, dict]] = {}


def _fetch_from_naver(ticker: str) -> dict | None:
    """네이버 폴링 API 호출. 실패/장시간 외/형식 이상 등 어떤 사유로든 실패하면 None(예외를 던지지 않음)."""
    try:
        resp = requests.get(
            NAVER_REALTIME_URL.format(ticker=ticker),
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        payload = resp.json()
    except (requests.RequestException, ValueError):
        return None

    datas = payload.get("datas") or []
    if not datas:
        return None
    row = datas[0]
    if row.get("marketStatus") != "OPEN":
        return None

    try:
        return {
            "current_price": float(row["closePriceRaw"]),
            "change_amount": float(row["compareToPreviousClosePriceRaw"]),
            "change_pct": float(row["fluctuationsRatioRaw"]),
            "as_of": row["localTradedAt"],
            "is_realtime": True,
            "source": "naver",
        }
    except (KeyError, TypeError, ValueError):
        return None


def _fallback_from_db(db, company: Company) -> dict | None:
    """네이버 조회가 실패했을 때 쓰는 폴백. DB에 저장된(FinanceDataReader로 수집된) 최근 종가를 쓴다."""
    row = (
        db.query(StockPrice)
        .filter(StockPrice.company_id == company.id)
        .order_by(StockPrice.price_date.desc())
        .first()
    )
    if row is None:
        return None
    return {
        "current_price": float(row.close_price),
        "change_amount": None,
        "change_pct": float(row.change_pct) if row.change_pct is not None else None,
        "as_of": row.price_date.isoformat(),
        "is_realtime": False,
        "source": "fallback",
    }


def get_realtime_price_data(db, company: Company) -> dict | None:
    """종목의 실시간(또는 폴백) 시세 스냅샷을 돌려준다. 둘 다 실패하면 None.

    종목별로 CACHE_TTL_SECONDS 동안 캐싱하므로, 그 안에 다시 부르면 상위 소스를 재호출하지 않는다.
    """
    cached = _cache.get(company.ticker)
    if cached is not None and time.monotonic() - cached[0] < CACHE_TTL_SECONDS:
        return cached[1]

    data = _fetch_from_naver(company.ticker) or _fallback_from_db(db, company)
    if data is not None:
        _cache[company.ticker] = (time.monotonic(), data)
    return data
