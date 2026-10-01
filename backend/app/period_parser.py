"""질문 텍스트에 들어있는 기간 표현을 인식해 명시적 날짜 범위(및 tool에 넘길 period_days 근사치)로 바꾼다.

## 배경 (FR-01 "자연어 투자 질문 분석"의 기간 식별 부분)

지금까지는 기간을 해석하는 코드가 전혀 없어서 LLM이 "3개월"을 `period_days=90`처럼 알아서 숫자로 바꿔 tool에
넘겼다. 측정 결과(README "FR-01 인수조건별 검증") "3개월"을 `period_days=3`(3일)으로 잘못 바꾸는 경우가 6회 중
2회 있었다. 이 모듈은 질문에서 흔한 기간 표현을 **코드로 직접** 인식해서, 인식에 성공하면 그 값을 LLM 추론보다
우선해서 쓰도록(`app/agent.py`의 `_apply_period_override`) 한다.

## 지원 범위 (이 밖의 표현은 전부 인식하지 못한 것으로 보고 None을 돌려준다 - 예외를 던지지 않는다)

- **고정 상대 표현**: 오늘, 어제, 이번주/이번 주, 지난주/지난 주, 이번달/이번 달, 지난달/지난 달, 올해, 작년
  (구분자 공백 유무 둘 다 인식한다 - 실제 질문에 "이번 달"처럼 띄어 쓰는 경우가 있었다,
  `scripts/benchmark_routing.py`의 `market_ho` 참고).
- **수량 표현**: 아라비아 숫자 + 단위(일/주/개월/년). 예: "3일", "2주간", "3개월 동안", "1년".
  한글 숫자("삼개월")나 숫자 없는 표현("일주일", "한 달")은 인식하지 않는다.
- 질문에 인식 가능한 표현이 여러 개 있으면 **질문에서 가장 먼저 등장한 것 하나만** 쓴다(뒤에 나온 표현은 무시).
- "N일 전"처럼 **특정 시점**을 가리키는 표현(기간이 아니라 점)은 일부러 인식하지 않는다(숫자+단위 바로 뒤에 "전"이
  오면 제외). "재작년"(작년의 부분 문자열), "지지난달"/"지지난주"도 다른 뜻이라 제외한다.
- **상한**: 비현실적으로 큰 숫자(예: "2024년"의 "2024")는 기간 수량이 아니라 연도 등 다른 의미일 가능성이 높아
  인식하지 않는다(일 3650/주 520/개월 120/년 50까지만).

## period_days 계산

`stock_tool`/`market_tool`의 `period_days`는 달력 일수가 아니라 **최근 N개 거래일(행) 수**로 쓰인다(두 tool 모두
날짜로 필터링하지 않고 `ORDER BY price_date DESC LIMIT period_days`로 가장 최근 N행을 가져온다). 그래서 이 모듈은
날짜 범위를 구한 뒤, 그 범위의 **평일(월~금) 수**를 세서 돌려준다(`_business_days_between`). 공휴일은 고려하지
않는 근사치다(KRX 휴장일 캘린더가 없음) - "거래일 수"의 정확한 값이 아니라 기존 LLM 추측보다 훨씬 믿을 만한
근사치를 제공하는 것이 목표다. 상한 60(두 tool의 `to_int(..., hi=60)`)을 넘는 값(예: "올해", "작년")은 이전처럼
조용히 60으로 잘린다 - 이 모듈이 그 제한 자체를 바꾸지는 않는다.
"""

import calendar
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")


@dataclass
class ParsedPeriod:
    start_date: date
    end_date: date
    # start_date~end_date 사이 평일(근사 거래일) 수, 최소 1. tool의 period_days로 바로 쓴다.
    period_days: int
    # 질문에서 실제로 인식한 원문 조각(로깅/디버깅/테스트용).
    matched_text: str


def _today(now: datetime | None = None) -> date:
    return (now or datetime.now(KST)).date()


def _business_days_between(start: date, end: date) -> int:
    """start~end(포함) 사이의 평일(월~금) 수. 공휴일은 고려하지 않는 근사치다."""
    if start > end:
        start, end = end, start
    total_days = (end - start).days + 1
    weeks, remainder = divmod(total_days, 7)
    count = weeks * 5
    weekday = start.weekday()
    for i in range(remainder):
        if (weekday + i) % 7 < 5:
            count += 1
    return count


# --- 고정 상대 표현 ---------------------------------------------------------


def _this_week(today: date) -> tuple[date, date]:
    return today - timedelta(days=today.weekday()), today


def _last_week(today: date) -> tuple[date, date]:
    this_monday = today - timedelta(days=today.weekday())
    last_monday = this_monday - timedelta(days=7)
    return last_monday, this_monday - timedelta(days=1)


def _this_month(today: date) -> tuple[date, date]:
    return today.replace(day=1), today


def _last_month(today: date) -> tuple[date, date]:
    last_day_of_prev_month = today.replace(day=1) - timedelta(days=1)
    return last_day_of_prev_month.replace(day=1), last_day_of_prev_month


def _this_year(today: date) -> tuple[date, date]:
    return date(today.year, 1, 1), today


def _last_year(today: date) -> tuple[date, date]:
    return date(today.year - 1, 1, 1), date(today.year - 1, 12, 31)


# 공백 유무 둘 다 인식("이번달"/"이번 달"). "지난주"/"지난달"은 "지지난주"/"지지난달"(그 이전 주/달,
# 다른 뜻)의 일부가 아닐 때만 인식한다. "작년"도 "재작년"의 일부일 때는 제외한다.
_FIXED_PATTERNS: list[tuple[re.Pattern, "callable"]] = [
    (re.compile(r"오늘"), lambda t: (t, t)),
    (re.compile(r"어제"), lambda t: (t - timedelta(days=1), t - timedelta(days=1))),
    (re.compile(r"(?<!지)지난\s?주"), _last_week),
    (re.compile(r"이번\s?주"), _this_week),
    (re.compile(r"(?<!지)지난\s?달"), _last_month),
    (re.compile(r"이번\s?달"), _this_month),
    (re.compile(r"(?<!재)작년"), _last_year),
    (re.compile(r"올해"), _this_year),
]


# --- 수량 표현 ---------------------------------------------------------------

_MAX_QUANTITY = {"일": 3650, "주": 520, "개월": 120, "년": 50}
# 숫자+단위 바로 뒤에 "전"이 오면("10년 전") 기간(범위)이 아니라 특정 시점을 가리키는 표현이라 제외한다.
_QUANTITY_RE = re.compile(r"(\d+)\s?(일|주|개월|년)(?!\s?전)")


def _days_in_month(year: int, month: int) -> int:
    return calendar.monthrange(year, month)[1]


def _subtract_months(d: date, months: int) -> date:
    total = d.month - 1 - months
    year = d.year + total // 12
    month = total % 12 + 1
    day = min(d.day, _days_in_month(year, month))
    return date(year, month, day)


def _subtract_years(d: date, years: int) -> date:
    year = d.year - years
    day = d.day
    if d.month == 2 and d.day == 29 and not calendar.isleap(year):
        day = 28
    return date(year, d.month, day)


def _quantity_range(today: date, n: int, unit: str) -> tuple[date, date] | None:
    if not (1 <= n <= _MAX_QUANTITY[unit]):
        return None
    if unit == "일":
        start = today - timedelta(days=n - 1)
    elif unit == "주":
        start = today - timedelta(days=n * 7 - 1)
    elif unit == "개월":
        start = _subtract_months(today, n)
    else:  # "년"
        start = _subtract_years(today, n)
    return start, today


def _candidates(question: str, today: date) -> list[tuple[int, str, tuple[date, date]]]:
    found = []
    for pattern, to_range in _FIXED_PATTERNS:
        m = pattern.search(question)
        if m:
            found.append((m.start(), m.group(), to_range(today)))
    for m in _QUANTITY_RE.finditer(question):
        rng = _quantity_range(today, int(m.group(1)), m.group(2))
        if rng is not None:
            found.append((m.start(), m.group(), rng))
    return found


def parse_period(question: str, now: datetime | None = None) -> ParsedPeriod | None:
    """질문에서 기간 표현 하나를 인식해 돌려준다. 인식 못 하면 None(호출측은 기존 LLM 추론값을 그대로 쓴다).

    now는 테스트에서 "오늘"을 고정하기 위한 것으로, 생략하면 실제 현재 시각(KST)을 쓴다."""
    if not question:
        return None
    today = _today(now)
    found = _candidates(question, today)
    if not found:
        return None

    found.sort(key=lambda c: c[0])
    _, matched_text, (start, end) = found[0]
    period_days = max(1, _business_days_between(start, end))
    return ParsedPeriod(start_date=start, end_date=end, period_days=period_days, matched_text=matched_text)
