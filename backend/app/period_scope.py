"""질문에 명시된 기간이 보유 시세 범위보다 앞서는지 판단하고, 그런 질문의 답을 코드가 만든다.

배경: "삼성전자 2024년 3월 주가 알려줘"에 모델이 최근 종가(2026-10-14)를 "2024년 3월 주가는 235,000원"이라고 답했다
(평가 기준선 ref_04). 시세는 2026-06-26부터만 있어서 이런 질문은 답할 수 있는 값이 없다. 그래서 LLM이 숫자를 쓰기 전에
코드가 기간을 보유 범위와 비교한다.

- 범위 밖(요청 기간 끝 < 보유 시작일): "요청하신 기간의 시세 데이터는 제공하지 않습니다"와 보유 범위. 참고로 보유 데이터의
  마지막 종가를 덧붙이되 요청 기간의 값이 아니라고 밝힌다.
- 일부만 겹침(요청 시작 < 보유 시작일 <= 요청 끝): 겹치는 구간의 요약만(코드가 계산) 답하고, 데이터가 없는 구간을 밝힌다.
- 그 밖(범위 안이거나 기간을 말하지 않음): None. 기존 흐름 그대로다. 보유 범위 **뒤**(수집되지 않은 최근)는 판단하지 않는다 -
  "오늘 주가"는 가장 최근 데이터로 답하는 기존 동작을 바꾸지 않는다.

인식하는 기간: 연도("2024년"), 연+월("2024년 3월"), 날짜("2026년 3월 15일", "2026-03-15"), 연도 없는 월·날짜("6월", "3월 15일":
올해, 아직 오지 않은 달이면 작년), "작년", "재작년", "작년 이맘때", "N년/N개월/N주/N일 전"(그날 하루), "X부터"(X의 시작~오늘),
그리고 app.period_parser.parse_period가 인식하는 상대 기간("올해", "1년", "반년", "3개월" 등).
"""

import re
from calendar import monthrange
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.period_parser import parse_period

# 시세(가격·거래량·등락)를 묻는 질문만 대상으로 한다. "2024년 실적 뉴스"처럼 시세가 아닌 질문은 기존 흐름이다.
PRICE_INTENT_RE = re.compile(r"주가|종가|시가|고가|저가|시세|가격|거래량|등락|수익률|올랐|내렸|떨어|상승|하락|흐름|추이|움직")

_YMD_RE = re.compile(r"((?:19|20)\d{2})\s*(?:년\s*|[-.]\s*)(\d{1,2})\s*(?:월\s*|[-.]\s*)(\d{1,2})\s*일?")
_YM_RE = re.compile(r"((?:19|20)\d{2})\s*년\s*(\d{1,2})\s*월")
_Y_RE = re.compile(r"((?:19|20)\d{2})\s*년(?!\s*(?:전|간|동안))")
_MD_RE = re.compile(r"(?<!\d)(\d{1,2})\s*월\s*(\d{1,2})\s*일")
_M_RE = re.compile(r"(?<!\d)(\d{1,2})\s*월(?!\s*\d|요일|간|별)")
_AGO_RE = re.compile(r"(\d{1,3})\s*(년|개월|주|일)\s*전")
_FROM_RE = re.compile(r"^\s*(?:부터|이후|이래)")


@dataclass
class RequestedPeriod:
    start: date
    end: date
    label: str  # 질문에 쓰인 표현 그대로("2024년 3월", "작년", "1년")


def _safe_date(y: int, m: int, d: int) -> date | None:
    try:
        return date(y, m, d)
    except ValueError:
        return None


def _shift_months(d: date, months: int) -> date:
    total = d.year * 12 + d.month - 1 - months
    y, m = divmod(total, 12)
    return date(y, m + 1, min(d.day, monthrange(y, m + 1)[1]))


def _past_year_for_month(month: int, today: date) -> int:
    return today.year if month <= today.month else today.year - 1


def requested_period(question: str, today: date) -> RequestedPeriod | None:
    """질문에서 기간 표현 하나(가장 먼저 나온 것)를 날짜 범위로 바꾼다. 없으면 None."""
    q = question or ""
    found: list[tuple[int, int, date, date, str]] = []  # (시작 위치, 끝 위치, start, end, label)
    taken: list[tuple[int, int]] = []

    def add(m, start, end, span=None):
        st, en = span or m.span()
        if start is None or any(not (en <= a or st >= b) for a, b in taken):
            return
        taken.append((st, en))
        found.append((st, en, start, end, q[st:en].strip()))

    for m in _YMD_RE.finditer(q):
        d = _safe_date(*map(int, m.groups()))
        add(m, d, d)
    for m in _YM_RE.finditer(q):
        y, mo = int(m.group(1)), int(m.group(2))
        if 1 <= mo <= 12:
            add(m, date(y, mo, 1), date(y, mo, monthrange(y, mo)[1]))
    for m in _Y_RE.finditer(q):
        y = int(m.group(1))
        add(m, date(y, 1, 1), date(y, 12, 31))
    for m in _MD_RE.finditer(q):
        mo, d = int(m.group(1)), int(m.group(2))
        if 1 <= mo <= 12:
            day = _safe_date(_past_year_for_month(mo, today), mo, d)
            add(m, day, day)
    for m in _M_RE.finditer(q):
        mo = int(m.group(1))
        if 1 <= mo <= 12:
            y = _past_year_for_month(mo, today)
            add(m, date(y, mo, 1), min(date(y, mo, monthrange(y, mo)[1]), today))
    for m in re.finditer(r"작년\s*이맘때", q):
        day = _shift_months(today, 12)
        add(m, day, day)
    for m in re.finditer(r"재작년", q):
        add(m, date(today.year - 2, 1, 1), date(today.year - 2, 12, 31))
    for m in re.finditer(r"(?<!재)작년", q):
        add(m, date(today.year - 1, 1, 1), date(today.year - 1, 12, 31))
    for m in _AGO_RE.finditer(q):
        n, unit = int(m.group(1)), m.group(2)
        if unit == "년":
            day = _shift_months(today, 12 * n)
        elif unit == "개월":
            day = _shift_months(today, n)
        else:
            day = today - timedelta(days=n * (7 if unit == "주" else 1))
        add(m, day, day)

    # "X부터(지금까지)"는 X의 시작부터 오늘까지
    for i, (st, en, start, end, label) in enumerate(found):
        if _FROM_RE.match(q[en:]):
            found[i] = (st, en, start, today, label + "부터")

    # 호출측이 정한 today를 그대로 쓴다(생략하면 parse_period가 실제 현재 시각을 써서 today와 어긋난다)
    parsed = parse_period(q, datetime.combine(today, time(12), tzinfo=ZoneInfo("Asia/Seoul")))
    if parsed is not None:
        pos = q.find(parsed.matched_text)
        end_pos = pos + len(parsed.matched_text)
        if pos >= 0 and all(end_pos <= a or pos >= b for a, b in taken):
            found.append((pos, end_pos, parsed.start_date, parsed.end_date, parsed.matched_text))
    if not found:
        return None
    _, _, start, end, label = min(found, key=lambda f: f[0])
    return RequestedPeriod(start=start, end=end, label=label)


@dataclass
class Scope:
    kind: str  # "outside" | "partial"
    requested: RequestedPeriod
    data_start: date
    data_end: date


def classify(requested: RequestedPeriod | None, data_start: date | None, data_end: date | None) -> Scope | None:
    """요청 기간이 보유 시작일보다 전부 앞서면 outside, 시작일을 걸치면 partial, 그 밖은 None(기존 흐름)."""
    if requested is None or data_start is None or data_end is None:
        return None
    if requested.end < data_start:
        return Scope("outside", requested, data_start, data_end)
    if requested.start < data_start <= requested.end:
        return Scope("partial", requested, data_start, data_end)
    return None


def _range_text(r: RequestedPeriod) -> str:
    if r.start == r.end:
        return f"{r.label}: {r.start.isoformat()}"
    return f"{r.label}: {r.start.isoformat()}~{r.end.isoformat()}"


def outside_answer(name: str, scope: Scope, latest: dict | None) -> str:
    """요청 기간 전체가 보유 범위 밖일 때의 답. latest={"price_date", "close_price"}(보유 데이터 마지막 거래일)."""
    text = (
        f"요청하신 기간({_range_text(scope.requested)})의 {name} 시세 데이터는 제공하지 않습니다. "
        f"보유한 데이터는 {scope.data_start.isoformat()}부터 {scope.data_end.isoformat()}까지입니다."
    )
    if latest:
        text += (
            f"\n\n참고로 보유 데이터의 마지막 거래일({latest['price_date']}) {name} 종가는 {latest['close_price']:,.0f}원입니다. "
            "요청하신 기간의 값이 아닙니다."
        )
    return text


def partial_answer(name: str, scope: Scope, prices: list[dict], summary: str) -> str:
    """요청 기간의 일부만 보유 범위와 겹칠 때의 답. prices는 겹치는 구간의 시세(날짜 오름차순), summary는 그 구간의 요약 문장."""
    r = scope.requested
    missing_end = scope.data_start - timedelta(days=1)
    parts = [
        f"요청하신 기간({_range_text(r)}) 중 {r.start.isoformat()}~{missing_end.isoformat()} 구간은 시세 데이터가 없습니다. "
        f"보유한 데이터는 {scope.data_start.isoformat()}부터 {scope.data_end.isoformat()}까지이며, 아래는 데이터가 있는 구간"
        f"({prices[0]['price_date']}~{prices[-1]['price_date']})만의 값입니다."
    ]
    if r.end > scope.data_end:
        parts[0] += f" {scope.data_end.isoformat()} 이후는 아직 수집되지 않았습니다."
    parts.append(summary)
    return "\n\n".join(parts)
