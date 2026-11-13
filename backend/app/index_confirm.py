"""코스피·코스닥 지수의 저장 전 확정 검증(2026-11-13, 50차).

## 왜 필요한가
- 지수 원천은 FinanceDataReader `KrxIndexReaderCache`(GitHub `FinanceData/fdr_krx_data_cache`의 연도별 CSV, 제3자 캐시)다.
- 캐시는 하루 2~3번 갱신된다(커밋 기록 2026-11-09~11-13): 약 07시, 13:39~14:54(장중), 21:10~23:19(장 마감 뒤).
  장중 갱신이 만든 그날 행은 장중 값이다. 예: 11-12 14:43 코스피 5,305.47 -> 23:01 5,342.73(확정, 네이버 지수 일별과 같음).
- 커밋 시점별 파일을 비교하면 장중 값이 남는 날은 **캐시의 마지막 행 하나뿐**이다. 다음 갱신이 그 행을 확정값으로 바꾸고,
  그보다 앞선 날짜는 커밋마다 같다(11-10~11-13 커밋 9개 확인).
- 그래서 14시~23시에 수집하면 장중 값이 그날 종가로 저장됐다(검증 DB 11-11, 개발 DB 11-03). 시장 요약은 15:30 뒤 그 행을 "latest"로 보였다.

## 규칙(collectors.fetch_and_save_market_index가 저장 전에 적용)
- 오늘 행은 그날 정규장 마감(krx_calendar.close_time, 특수일 포함) + CONFIRM_DELAY 전에는 저장하지 않는다(intraday).
- 네이버 지수 일별(m.stock.naver.com/api/index/{KOSPI|KOSDAQ}/price, 비공식)과 날짜가 겹치는 행은 값이 같을 때만 저장한다
  (TOLERANCE 안). 다르면 저장하지 않는다(mismatch). 네이버는 15:30 뒤 그날 확정 종가를 보인다(11-13 관찰).
- 네이버를 읽지 못했거나 네이버에 그 날짜가 없으면, 수집한 행 중 **마지막 행**만 저장하지 않는다(unverified).
  마지막 행보다 앞선 날짜는 캐시에 그 뒤 날짜가 있다 = 그날 마감 뒤에 갱신됐다는 뜻이라 확정값이다.
- 저장하지 않은 날은 기존 DB 값을 그대로 둔다(덮어쓰지 않는다). 다음 실행에서 확정되면 저장된다.
- 0이나 임의의 값을 만들지 않는다. 네이버 값으로 대신 저장하지도 않는다(원천을 섞지 않는다).

## 이 모듈이 하지 않는 것
- 공식 원천(KRX 정보데이터시스템)은 로그인이 필요해 쓰지 않는다. 네이버도 비공식 API라 "독립 대조"일 뿐 공식 확인은 아니다.
- 이미 저장된 과거 행을 고치지 않는다(개발 DB 11-12까지는 네이버와 모두 같음, 50차 확인).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from app import http_retry, krx_calendar

NAVER_INDEX_URL = "https://m.stock.naver.com/api/index/{code}/price"
NAVER_CODES = {"KS11": "KOSPI", "KQ11": "KOSDAQ"}
PAGE_SIZE = 20  # 최근 20거래일. 60보다 크면 400(종목 일별과 같은 제한)
REQUEST_TIMEOUT_S = 10
HEADERS = {"User-Agent": "Mozilla/5.0"}
CONFIRM_DELAY = timedelta(minutes=10)  # 마감 직후 몇 분은 지수 종가 게시 전일 수 있다
TOLERANCE = 0.005  # 지수는 소수 둘째 자리까지다

CONFIRMED = "confirmed"  # 네이버와 같다
TRUSTED_OLDER = "older_than_latest"  # 대조 범위 밖이지만 마지막 행보다 앞선 날(캐시가 그 뒤에 갱신됨)
INTRADAY = "intraday"
MISMATCH = "mismatch"
UNVERIFIED = "unverified"
HELD = (INTRADAY, MISMATCH, UNVERIFIED)


@dataclass(frozen=True)
class Decision:
    day: date
    value: float
    status: str
    reference: float | None = None  # 대조한 네이버 값

    @property
    def held(self) -> bool:
        return self.status in HELD

    def as_dict(self) -> dict:
        return {"date": self.day.isoformat(), "value": self.value, "status": self.status, "reference": self.reference}


def _num(v) -> float:
    return float(str(v).replace(",", ""))


def fetch_naver_closes(index_code: str, get=None) -> dict[date, float] | None:
    """네이버 지수 일별 최근 PAGE_SIZE거래일 {날짜: 종가}. 지원하지 않는 코드면 None, 실패하면 예외."""
    code = NAVER_CODES.get(index_code)
    if code is None:
        return None
    get = get or (lambda url, params: http_retry.get(url, params=params, headers=HEADERS, timeout=REQUEST_TIMEOUT_S,
                                                       label="naver index daily").json())
    rows = get(NAVER_INDEX_URL.format(code=code), {"pageSize": PAGE_SIZE, "page": 1})
    if not isinstance(rows, list):
        raise ValueError("unexpected naver index payload")
    return {date.fromisoformat(r["localTradedAt"][:10]): _num(r["closePrice"]) for r in rows}


def intraday(day: date, now: datetime) -> bool:
    """그날 정규장 마감 + CONFIRM_DELAY 전인가(오늘 또는 그 뒤 날짜). now는 KST로 본다."""
    now = krx_calendar._kst(now)
    if day > now.date():
        return True
    if day < now.date():
        return False
    close_at = datetime.combine(day, krx_calendar.close_time(day), tzinfo=krx_calendar.KST) + CONFIRM_DELAY
    return now < close_at


def decide(records: list[dict], naver: dict[date, float] | None, now: datetime) -> list[Decision]:
    """records: 지수 레코드(price_date, close_price) 날짜 오름차순. naver: 네이버 {날짜: 종가}, 읽지 못했으면 None."""
    if not records:
        return []
    last = max(r["price_date"] for r in records)
    out = []
    for r in records:
        d, v = r["price_date"], float(r["close_price"])
        ref = (naver or {}).get(d)
        if intraday(d, now):
            status = INTRADAY
        elif ref is not None:
            status = CONFIRMED if abs(v - ref) <= TOLERANCE else MISMATCH
        elif d < last:
            status = TRUSTED_OLDER
        else:
            status = UNVERIFIED
        out.append(Decision(d, v, status, ref))
    return out


def split(records: list[dict], naver: dict[date, float] | None, now: datetime) -> tuple[list[dict], list[Decision]]:
    """(저장할 레코드, 저장하지 않은 날의 판정)."""
    decisions = {x.day: x for x in decide(records, naver, now)}
    keep = [r for r in records if not decisions[r["price_date"]].held]
    return keep, [x for x in decisions.values() if x.held]
