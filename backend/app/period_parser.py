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
- **고유어 수량 표현**(2026-10-27 추가): "한 달"·"두 달"·"석 달"(한/두/세/석/네/넉/다섯/여섯 + 달), "한 주"·"두 주",
  "일주일", "반년"(6개월), "열흘"(10일), "보름"(15일). "N개월"과 같은 방식으로 범위를 만든다("한 달" = "1개월").
  "한 달러", "신한 주가"처럼 다른 말의 일부는 제외한다. "하루"는 "하루 만에"처럼 기간이 아닌 경우가 많아 인식하지 않는다.
  한자어 숫자("삼개월")는 인식하지 않는다.
- 질문에 인식 가능한 표현이 여러 개 있으면 **질문에서 가장 먼저 등장한 것 하나만** 쓴다(뒤에 나온 표현은 무시).
- "N일 전"처럼 **특정 시점**을 가리키는 표현(기간이 아니라 점)은 일부러 인식하지 않는다(숫자+단위 바로 뒤에 "전"이
  오면 제외). "재작년"(작년의 부분 문자열), "지지난달"/"지지난주"도 다른 뜻이라 제외한다.
- **ETF 이름 속 숫자는 기간이 아니다**: "KODEX 200 주가"의 "200 주"(200주), "KODEX CD1년금리..."의 "1년"처럼 ETF 이름에
  숫자+단위 모양이 섞여 있다. 그래서 파싱 전에 ETF 이름 구간을 지운다(`app.etf.mask_etf_names`: 브랜드로 시작하는 이름만 지우고,
  "KODEX 200 3개월"의 "3개월"처럼 이름 뒤의 진짜 기간 표현은 남긴다). 주식 질문은 바뀌지 않는다.
- **상한**: 비현실적으로 큰 숫자(예: "2024년"의 "2024")는 기간 수량이 아니라 연도 등 다른 의미일 가능성이 높아
  인식하지 않는다(일 3650/주 520/개월 120/년 50까지만).
- **달력 표현은 수량·고정 표현이 아니다**(2026-10-31): "올해 1분기", "작년 3월", "작년 이맘때", "24년 3월"은 이 함수가 일부("올해", "24년")만
  읽으면 다른 기간이 되므로 None을 돌려준다. 값은 app.period_scope.requested_period가 이 모듈의 정규식으로 계산한다
  (호출측은 parse_period가 None이어야 "최근 창"이 아니라 날짜로 조회해야 할 기간임을 안다).
- **두 자리 연도**: "24년 3월"처럼 뒤에 월·분기·반기가 오면 언제나 20YY년이다. 숫자만 있는 "24년"은 20~올해 연도(YY)이면 연도이고
  ("최근 24년", "24년간"은 수량), 그 밖은 수량이다("5년", "30년"). 올해를 넘는 "27년 3월"은 미래 연도로 보고(19YY로 고치지 않는다) 데이터 없음 안내로 간다.

## period_days 계산

`stock_tool`/`market_tool`의 `period_days`는 달력 일수가 아니라 **최근 N개 거래일(행) 수**로 쓰인다(두 tool 모두
날짜로 필터링하지 않고 `ORDER BY price_date DESC LIMIT period_days`로 가장 최근 N행을 가져온다). 그래서 이 모듈은
날짜 범위를 구한 뒤, 그 범위의 **평일(월~금) 수**를 세서 돌려준다(`_business_days_between`). 공휴일은 고려하지
않는 근사치다(KRX 휴장일 캘린더가 없음) - "거래일 수"의 정확한 값이 아니라 기존 LLM 추측보다 훨씬 믿을 만한
근사치를 제공하는 것이 목표다. stock_tool·market_tool은 2026-10-31부터 긴 기간을 자르지 않는다(stock_tool이 LLM에 넘기는 행만 최근 60개).
"""

import calendar
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from app.etf import mask_etf_names

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


# --- 달력 표현(app.period_scope.requested_period와 함께 쓴다) --------------------

YEAR_WORD = r"(재작년|작년|지난해|올해|금년)"
# 연도 앞부분. 그룹 1: 네 자리 또는 두 자리 연도, 그룹 2: 올해·작년 같은 말
YEAR_PREFIX = r"(?:(?<![\d.,])((?:19|20)?\d{2})\s*년\s*|" + YEAR_WORD + r"\s*)?"
WORD_MONTH_RE = re.compile(YEAR_WORD + r"\s*(\d{1,2})\s*월(?:\s*(\d{1,2})\s*일)?")
QUARTER_RE = re.compile(YEAR_PREFIX + r"([1-4])\s*분기")
HALF_RE = re.compile(YEAR_PREFIX + r"([상하])반기")
SAME_MONTH_RE = re.compile(r"(?:작년|지난해)\s*(?:같은\s*달|동월)")
SAME_TIME_RE = re.compile(r"작년\s*이맘때")
# "24년 3월", "24년 3월 15일"
SHORT_YEAR_MONTH_RE = re.compile(r"(?<![\d.,])(\d{2})\s*년\s*(\d{1,2})\s*월(?:\s*(\d{1,2})\s*일)?")
# 숫자만 있는 "24년". 수량("최근 24년", "24년간", "24년 동안", "24년 전")이면 제외한다
_SHORT_YEAR_RE = re.compile(r"(?<![\d.,])(?<!최근\s)(?<!지난\s)(?<!과거\s)(\d{2})\s*년(?!\s*(?:간|동안|째|치|전(?!체)))")
_COMPOUND_RES = (WORD_MONTH_RE, QUARTER_RE, HALF_RE, SAME_MONTH_RE, SAME_TIME_RE, SHORT_YEAR_MONTH_RE)


def full_year(text: str) -> int:
    """두 자리 연도는 언제나 20YY다("24" -> 2024, "27" -> 2027, "95" -> 2095). 19YY로 고치지 않는다(보유 시세보다 앞서 어차피 답이 없다)."""
    return int(text) if len(text) == 4 else 2000 + int(text)


def short_year_spans(question: str, today: date) -> list[tuple[int, int, int]]:
    """숫자만 있는 두 자리 연도("24년 주가")의 (시작, 끝, 연도). 20~올해 연도일 때만이다("5년", "30년"은 수량)."""
    out = []
    for m in _SHORT_YEAR_RE.finditer(question):
        if 20 <= int(m.group(1)) <= today.year % 100:
            out.append((m.start(), m.end(), full_year(m.group(1))))
    return out


def calendar_spans(question: str, today: date) -> list[tuple[int, int]]:
    """수량·고정 표현으로 읽으면 안 되는 달력 표현의 위치."""
    spans = [m.span() for pattern in _COMPOUND_RES for m in pattern.finditer(question)]
    return spans + [s[:2] for s in short_year_spans(question, today)]


# --- 수량 표현 ---------------------------------------------------------------

_MAX_QUANTITY = {"일": 3650, "주": 520, "개월": 120, "년": 50}
# 숫자+단위 바로 뒤에 "전"이 오면("10년 전") 기간(범위)이 아니라 특정 시점을 가리키는 표현이라 제외한다.
# "월" 바로 뒤의 숫자도 제외한다: "2026년 3월 15일"의 "15일"은 15일간이 아니라 날짜다(최근 10거래일로 답하던 ref_08, 2026-10-31).
_QUANTITY_RE = re.compile(r"(?<!\d)(?<!월)(?<!월\s)(\d+)\s?(일|주|개월|년)(?!\s?전)")


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


# 고유어 수량 표현: (정규식, 수량 함수, 단위). 앞이 한글이면("신한 주가"의 '한') 수량이 아니다.
_NATIVE_NUM = {"한": 1, "두": 2, "세": 3, "석": 3, "네": 4, "넉": 4, "다섯": 5, "여섯": 6}
_NATIVE_PATTERNS: list[tuple[re.Pattern, "callable", str]] = [
    (re.compile(r"(?<![가-힣])(한|두|세|석|네|넉|다섯|여섯)\s?달(?!러)"), lambda m: _NATIVE_NUM[m.group(1)], "개월"),
    (re.compile(r"(?<![가-힣])(한|두|세|네)\s?주(?![가식주])"), lambda m: _NATIVE_NUM[m.group(1)], "주"),
    (re.compile(r"일주일"), lambda m: 1, "주"),
    (re.compile(r"반\s?년"), lambda m: 6, "개월"),
    (re.compile(r"열흘"), lambda m: 10, "일"),
    (re.compile(r"보름"), lambda m: 15, "일"),
]


def _candidates(question: str, today: date) -> list[tuple[int, str, tuple[date, date]]]:
    found = []
    taken = calendar_spans(question, today)

    def free(m: re.Match) -> bool:
        return all(m.end() <= a or m.start() >= b for a, b in taken)

    for pattern, to_range in _FIXED_PATTERNS:
        m = pattern.search(question)
        if m and free(m):
            found.append((m.start(), m.group(), to_range(today)))
    for m in _QUANTITY_RE.finditer(question):
        rng = _quantity_range(today, int(m.group(1)), m.group(2))
        if rng is not None and free(m):
            found.append((m.start(), m.group(), rng))
    for pattern, quantity, unit in _NATIVE_PATTERNS:
        for m in pattern.finditer(question):
            rng = _quantity_range(today, quantity(m), unit)
            if rng is not None:
                found.append((m.start(), m.group(), rng))
    return found


def parse_period(question: str, now: datetime | None = None) -> ParsedPeriod | None:
    """질문에서 기간 표현 하나를 인식해 돌려준다. 인식 못 하면 None(호출측은 기존 LLM 추론값을 그대로 쓴다).

    now는 테스트에서 "오늘"을 고정하기 위한 것으로, 생략하면 실제 현재 시각(KST)을 쓴다."""
    if not question:
        return None
    today = _today(now)
    found = _candidates(mask_etf_names(question), today)
    if not found:
        return None

    found.sort(key=lambda c: c[0])
    _, matched_text, (start, end) = found[0]
    period_days = max(1, _business_days_between(start, end))
    return ParsedPeriod(start_date=start, end_date=end, period_days=period_days, matched_text=matched_text)


# --- "기간을 말했는가"만 판단 ---------------------------------------------------

# parse_period가 값으로 바꾸지는 못하지만(위 "지원 범위" 참고) 사용자가 기간을 말한 것으로 봐야 하는 표현.
# "한 주"는 "신한 주가"처럼 뒤에 '가/식'이 붙는 경우를, "한 달"은 "한 달러"를 제외한다.
_INFORMAL_PERIOD_RE = re.compile(
    r"하루|이틀|사흘|나흘|닷새|열흘|보름|며칠|일주일|당일|금일|전일|장중|주간|월간|연간|분기|반\s?년|한\s?해"
    r"|[한두세네]\s?주(?![가식])|[한두세석넉]\s?달(?!러)"
)
_TICKER_CODE_RE = re.compile(r"(?<!\d)\d{6}(?!\d)")


def mentions_period(question: str) -> bool:
    """질문에 기간(또는 특정 시점)으로 볼 만한 표현이 하나라도 있으면 True.

    기본 기간을 코드가 채워도 되는지 판단하는 용도다(`app/agent.py`의 `_apply_default_period`): True면 사용자가
    기간을 말한 것이므로 기본값으로 덮어쓰지 않는다. parse_period가 인식하는 표현, 인식하지 못하는 구어 표현
    ("한 달", "일주일", "하루"), 그리고 숫자가 들어간 표현("5거래일", "3분기")을 모두 True로 본다 - 틀리게 True가
    나와도 이전처럼 LLM이 추론한 값을 그대로 쓸 뿐이라 안전한 쪽이다. 6자리 종목코드("005930")는 숫자로 치지 않는다."""
    if not question:
        return False
    if parse_period(question) is not None or calendar_spans(question, _today()):
        return True
    # ETF 이름 속 숫자("KODEX 200", "TIGER 미국S&P500")는 기간을 말한 것이 아니다
    text = mask_etf_names(question)
    if _INFORMAL_PERIOD_RE.search(text):
        return True
    return bool(re.search(r"\d", _TICKER_CODE_RE.sub("", text)))
