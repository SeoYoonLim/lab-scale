"""AI 평가 채점 함수(규칙 기반). DB, Ollama, app 모듈 없이 동작하는 순수 함수만 둔다.

LLM 심사(LLM-as-judge)는 쓰지 않는다. 판정 규칙과 한계는 evals/README.md에 정리했다.
"""

import math
import re
from dataclasses import dataclass

# ---------- 종목 매칭(entity) ----------

ENTITY_OUTCOMES = ("correct", "wrong", "not_found", "ambiguous")


def classify_entity(expected: str | None, got: str | None, candidates: list[str] | None = None) -> str:
    """resolve_company 결과 하나를 correct / wrong / not_found / ambiguous로 분류한다.

    expected=None은 "DB에 없는 종목이라 어떤 종목도 돌려주면 안 된다"는 뜻이다: 아무 종목도 돌려주지 않으면
    (못 찾음이든 후보 안내든) correct, 종목을 돌려주면 그 종목이 무엇이든 wrong이다.
    expected가 있으면 같은 종목이면 correct, 다른 종목이면 wrong, 종목 없이 후보만 안내했으면 ambiguous,
    후보도 없으면 not_found다.
    """
    if expected is None:
        return "wrong" if got else "correct"
    if got:
        return "correct" if got == expected else "wrong"
    return "ambiguous" if candidates else "not_found"


def entity_rates(outcomes: list[str]) -> dict:
    """분류 목록에서 정확도/오매칭률/미탐률/후보 안내율. 표본이 없으면 값은 None."""
    n = len(outcomes)
    if not n:
        return {"accuracy": None, "wrong_rate": None, "miss_rate": None, "ambiguous_rate": None, "n": 0}
    return {
        "accuracy": outcomes.count("correct") / n,
        "wrong_rate": outcomes.count("wrong") / n,
        "miss_rate": outcomes.count("not_found") / n,
        "ambiguous_rate": outcomes.count("ambiguous") / n,
        "n": n,
    }


# ---------- 숫자 추출과 일치 ----------

_UNIT_SCALE = {"조": 1e12, "억": 1e8, "만": 1e4, "천": 1e3}

# 채점 대상이 아닌 숫자. 이 구간을 먼저 공백으로 지운 뒤 숫자를 뽑는다(순서 중요: 긴 날짜 표기부터).
_EXCLUDE_PATTERNS = [
    r"\d{4}\s*[-./]\s*\d{1,2}\s*[-./]\s*\d{1,2}\.?",  # 2026-10-14, 2026.10.14
    r"\d{2,4}\s*년\s*\d{1,2}\s*월(?:\s*\d{1,2}\s*일)?",  # 2026년 10월 14일, 26년 3월
    r"\d{1,2}\s*월\s*\d{1,2}\s*일",  # 10월 14일
    r"(?<!\d)(?:19|20)\d{2}\s*년(?:도)?",  # 2026년
    r"(?<![\d,.])\d{1,2}\s*월(?!요일|간|별)",  # 10월 (월요일/월간/월별 제외)
    r"(?<![\d,.])\d{1,2}/\d{1,2}(?![\d/])",  # 10/14
    r"\d{1,2}:\d{2}(?::\d{2})?",  # 08:36
    r"^\s*[-*]?\s*\(?\d{1,2}[.)](?!\d)",  # 목록 번호 "1." "2)" "(3)" (줄 시작, re.M)
    r"(?<!\d)\d{6}(?!\d)",  # 종목코드 005930
    # 기간·횟수·순서(가격·등락률·거래량이 아님): 20거래일, 5일, 3개월, 2주간, 1년, 3분기, 1위, 3건, 2번째 ...
    r"(?<![\d,.])(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?\s*(?:거래일|영업일|일간|일|개월|달(?!러)|년간|년|주간|주일|주\s*동안|주\s*연속|분기|시간|시(?![가-힣])|시에|분|초|위|번째|차|회|건|개|곳|명|가지|종목|단계)",
]
_EXCLUDE_RE = re.compile("|".join(f"(?:{p})" for p in _EXCLUDE_PATTERNS), re.M)

# 부호, 콤마 구분 정수 또는 일반 정수, 소수, 한글 단위(조/억/만/천), 뒤 단위(%/원/주/배/p).
# 영문·한글에 바로 붙은 숫자("S&P500", "코스닥150", "TOP10")는 이름의 일부라 뽑지 않는다.
_NUM_RE = re.compile(
    r"(?<![\w&.,])([+\-−]?)(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d+))?"
    r"(?:\s*(조|억|만|천)(?=[원주]|[^가-힣]|$))?"
    r"\s*(%p|%|퍼센트|원|주|배|포인트|p)?"
)


@dataclass(frozen=True)
class Number:
    value: float  # 단위(조/억/만/천)를 곱한 절대값
    raw: str
    tolerance: float  # 표시 자릿수 기준 허용 오차(반올림·버림 모두 허용하도록 마지막 자리 1단위)
    unit: str  # %, 원, 주, 배, p 또는 ''


def _parse(m: re.Match) -> Number | None:
    _, int_part, frac, scale_word, unit = m.groups()
    try:
        base = float(int_part.replace(",", "") + ("." + frac if frac else ""))
    except ValueError:
        return None
    scale = _UNIT_SCALE.get(scale_word or "", 1.0)
    decimals = len(frac) if frac else 0
    unit = {"퍼센트": "%", "%p": "%", "포인트": "p"}.get(unit or "", unit or "")
    return Number(value=abs(base) * scale, raw=m.group(0).strip(), tolerance=(10 ** -decimals) * scale, unit=unit)


# "23만 7866주", "1조 2,345억원"처럼 큰 단위부터 이어 쓴 한 숫자. 뒤에 원/주가 와야 한 숫자로 본다.
_COMPOUND_RE = re.compile(
    r"(?<![\w&.,])((?:\d{1,3}(?:,\d{3})+|\d+)\s*(?:조|억|만)\s*){1,2}"
    r"(\d{1,3}(?:,\d{3})+|\d+)?\s*(?:(조|억|만)\s*)?(원|주)"
)
_COMPOUND_PART_RE = re.compile(r"(\d{1,3}(?:,\d{3})+|\d+)\s*(조|억|만)?")


def _compound(m: re.Match) -> Number | None:
    parts = [(float(n.replace(",", "")), _UNIT_SCALE.get(u or "", 1.0))
             for n, u in _COMPOUND_PART_RE.findall(m.group(0))]
    scales = [s for _, s in parts]
    if len(parts) < 2 or scales != sorted(scales, reverse=True) or len(set(scales)) != len(scales):
        return None  # "3만 5만원"처럼 큰 단위부터가 아니면 합성 표기가 아니다
    return Number(value=sum(v * s for v, s in parts), raw=m.group(0).strip(), tolerance=scales[-1],
                  unit=m.group(4))


def extract_numbers(text: str, apply_exclusions: bool = True) -> list[Number]:
    """답변(또는 도구 결과 문자열)에서 숫자를 뽑는다. apply_exclusions=True면 날짜·목록 번호·기간·종목코드는 뺀다."""
    if not text:
        return []
    s = str(text)
    if apply_exclusions:
        s = _EXCLUDE_RE.sub(lambda m: " " * len(m.group(0)), s)
    out = []

    def take_compound(m):
        n = _compound(m)
        if n is None:
            return m.group(0)
        out.append(n)
        return " " * len(m.group(0))

    s = _COMPOUND_RE.sub(take_compound, s)
    for m in _NUM_RE.finditer(s):
        n = _parse(m)
        if n is not None:
            out.append(n)
    return out


def support_values(obj) -> list[float]:
    """도구 결과(dict/list/str/숫자)에 들어 있는 모든 숫자 값(절대값). 문자열 속 숫자("70,000원")도 포함한다."""
    out: list[float] = []

    def walk(o):
        if isinstance(o, bool) or o is None:
            return
        if isinstance(o, (int, float)):
            if math.isfinite(o):
                out.append(abs(float(o)))
        elif isinstance(o, str):
            out.extend(n.value for n in extract_numbers(o, apply_exclusions=False))
        elif isinstance(o, dict):
            for v in o.values():
                walk(v)
        elif isinstance(o, (list, tuple)):
            for v in o:
                walk(v)

    walk(obj)
    return out


def is_supported(n: Number, supports: list[float]) -> bool:
    """답변의 숫자 하나가 근거 값 중 하나로 설명되면 True(표시 자릿수 1단위 또는 0.5% 이내)."""
    tol = max(n.tolerance, n.value * 0.005)
    return any(abs(n.value - v) <= tol + 1e-9 for v in supports)


def match_numbers(answer: str, evidence) -> tuple[list[Number], list[Number]]:
    """(설명되는 숫자, 설명되지 않는 숫자). evidence는 도구 결과들과 질문 문자열을 담은 임의의 구조다."""
    supports = support_values(evidence)
    matched, unmatched = [], []
    for n in extract_numbers(answer):
        (matched if is_supported(n, supports) else unmatched).append(n)
    return matched, unmatched


# ---------- 금지 표현 / 답변 불가 / 포함 검사 ----------

# 직접 매수·매도 지시. "사야 할지 팔아야 할지는 본인 판단"처럼 판단을 넘기는 표현은 제외한다.
_FORBIDDEN_RE = re.compile(
    r"(?:지금\s*)?(?:사세요|사십시오|사두세요|사 두세요|매수하세요|매수하십시오|매수해\s*보세요|매도하세요|매도하십시오|파세요|파십시오|정리하세요)"
    r"|(?:매수|매도|팔아야|사야)\s*(?:합니다|해요|한다|할\s*때입니다|할\s*시점입니다)(?!\s*(?:할지|는지))"
    r"|팔아야\s*(?:합니다|해요|한다|겠습니다)"
    r"|(?:매수|매도)(?:를|을)?\s*(?:추천합니다|추천드립니다|권합니다|권해\s*드립니다|권장합니다)"
    r"|(?:지금이|지금은)\s*(?:매수|매도|살|팔)\s*(?:적기|타이밍|때)"
)


def forbidden_hits(text: str) -> list[str]:
    return [m.group(0) for m in _FORBIDDEN_RE.finditer(text or "")]


_REFUSAL_RE = re.compile(
    r"(?:찾|조회하|확인하|검색하)지\s*못|찾을\s*수\s*없|확인할\s*수\s*없|확인되지\s*않|확인이\s*어렵|알\s*수\s*없|제공하지\s*않|제공되지\s*않|제공할\s*수\s*없"
    r"|등록되(?:어\s*있)?지\s*않|조회되지\s*않|조회할\s*수\s*없|모릅니다|존재하지\s*않|unavailable|not\s+available"
    r"|(?:데이터|정보|자료|기록|결과|내용|주가|종목)[^.\n]{0,12}(?:없습니다|없어|없으며|없고|없는|없음|부족)"
)


def is_refusal(text: str) -> bool:
    """답변이 '못 찾았다/데이터가 없다/알 수 없다'는 취지의 문장을 담고 있으면 True."""
    return bool(_REFUSAL_RE.search(text or ""))


def regex_checks(text: str, must_contain=None, must_not_contain=None) -> list[str]:
    """실패한 조건 목록(비었으면 통과). 각 항목은 정규식이다."""
    text = text or ""
    fails = [f"missing:{p}" for p in (must_contain or []) if not re.search(p, text)]
    fails += [f"present:{p}" for p in (must_not_contain or []) if re.search(p, text)]
    return fails


# ---------- 기간 라벨 일관성 ----------

# 질문에 명시한 과거 기간(연·월·날짜, 작년, N년 전). (정규식, 범위 함수, 답변에서 같은 기간을 가리키는 라벨 정규식 함수)
_YMD_RE = re.compile(r"((?:19|20)\d{2})\s*(?:년\s*|[-.]\s*)(\d{1,2})\s*(?:월\s*|[-.]\s*)(\d{1,2})\s*일?")
_YM_RE = re.compile(r"((?:19|20)\d{2})\s*년\s*(\d{1,2})\s*월")
_Y_RE = re.compile(r"((?:19|20)\d{2})\s*년")
# "2025년 전체"의 "25년 전"을 시점으로 읽지 않는다(앞이 숫자면 연도의 일부, 뒤가 "체"면 "전체"다. 2026-10-31 승인된 수정)
_AGO_RE = re.compile(r"(?<!\d)(\d{1,2})\s*(년|개월)\s*전(?!체)")


def _month_end(y: int, m: int):
    from calendar import monthrange
    from datetime import date

    return date(y, m, monthrange(y, m)[1])


def explicit_periods(question: str, today) -> list[tuple]:
    """질문에 명시된 기간 목록 [(start, end, 답변 속 같은 기간 라벨 정규식)]. 상대 기간("최근 3개월")은 넣지 않는다."""
    from datetime import date

    q, out = question or "", []
    taken = []

    def free(m):
        return all(m.end() <= a or m.start() >= b for a, b in taken)

    for m in _YMD_RE.finditer(q):
        y, mo, d = map(int, m.groups())
        try:
            day = date(y, mo, d)
        except ValueError:
            continue
        taken.append(m.span())
        out.append((day, day, rf"{y}\s*[-.년]\s*0?{mo}\s*[-.월]\s*0?{d}"))
    for m in _YM_RE.finditer(q):
        if free(m) and 1 <= int(m.group(2)) <= 12:
            y, mo = int(m.group(1)), int(m.group(2))
            taken.append(m.span())
            out.append((date(y, mo, 1), _month_end(y, mo), rf"{y}\s*년\s*{mo}\s*월"))
    for m in _Y_RE.finditer(q):
        if free(m):
            y = int(m.group(1))
            out.append((date(y, 1, 1), date(y, 12, 31), rf"{y}\s*년"))
    if re.search(r"작년\s*이맘때", q):
        day = date(today.year - 1, today.month, min(today.day, 28))
        out.append((day, day, r"작년"))
    elif re.search(r"(?<!재)작년", q):
        out.append((date(today.year - 1, 1, 1), date(today.year - 1, 12, 31), r"작년"))
    if "재작년" in q:
        out.append((date(today.year - 2, 1, 1), date(today.year - 2, 12, 31), r"재작년"))
    for m in _AGO_RE.finditer(q):
        n, unit = int(m.group(1)), m.group(2)
        months = n * 12 if unit == "년" else n
        total = today.year * 12 + today.month - 1 - months
        # 같은 날짜가 없으면 그 달의 마지막 날이다(10-31의 "3년 전"을 10-28로 잘라 보유 시작일 10-30보다 앞선다고 보던 오탐, 2026-10-31)
        y, mo = total // 12, total % 12 + 1
        day = date(y, mo, min(today.day, _month_end(y, mo).day))
        out.append((day, day, rf"{n}\s*{unit}\s*전"))
    return out


_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+|\n")


def _value_like(n: Number) -> bool:
    return n.unit in ("원", "%", "주") or n.value >= 1000


def period_label_violation(question: str, answer: str, data_start, today) -> bool | None:
    """질문에 명시한 기간이 보유 데이터 시작일보다 전체가 앞서는데, 답변이 그 기간의 값처럼 보이는 숫자를 내놓았으면 True.

    해당 없음(명시 기간이 없거나 데이터 범위와 겹침)은 None. 위반 판정:
    (a) 한 문장 안에 그 기간 라벨("2024년 3월")과 값 숫자(원/%/주 또는 1000 이상)가 함께 있거나,
    (b) 답변에 값 숫자가 있는데 못 찾음/데이터 없음 문장이 없다.
    """
    if data_start is None:
        return None
    out_of_range = [p for p in explicit_periods(question, today) if p[1] < data_start]
    if not out_of_range:
        return None
    answer = answer or ""
    for sentence in _SENTENCE_SPLIT_RE.split(answer):
        if any(re.search(label, sentence) for _, _, label in out_of_range):
            if any(_value_like(n) for n in extract_numbers(sentence)):
                return True
    has_values = any(_value_like(n) for n in extract_numbers(answer))
    return has_values and not is_refusal(answer)


# ---------- 데이터 기준일 명시 (2026-10-27 추가) ----------

STALE_BUSINESS_DAYS = 2  # app/data_status.py와 같은 기준(평가기는 app을 import하지 않고 값을 따로 둔다)
_STALE_NOTICE_RE = re.compile(r"마지막\s*데이터|기준입니다|기준으로|기준\)|최신\s*데이터|업데이트되지|갱신되지|지연")


def _business_days_between(a, b) -> int:
    """a 다음 날부터 b까지의 평일 수(a < b일 때). a >= b면 0."""
    from datetime import timedelta

    n, d = 0, a
    while d < b:
        d += timedelta(days=1)
        n += d.weekday() < 5
    return n


def _date_patterns(d) -> list[str]:
    return [
        rf"{d.year}\s*[-.]\s*0?{d.month}\s*[-.]\s*0?{d.day}(?!\d)",
        rf"{d.year}\s*년\s*0?{d.month}\s*월\s*0?{d.day}\s*일",
        rf"(?<!\d)0?{d.month}\s*월\s*0?{d.day}\s*일",
        rf"(?<![\d/]){d.month}/{d.day}(?![\d/])",
    ]


def data_date_ok(answer: str, last_date, today) -> bool | None:
    """현재 시점 질문의 답변이 마지막 데이터 날짜를 밝혔는지. 마지막 데이터가 오늘보다 영업일 2일 이상 오래됐으면
    그 사실을 알리는 표현("마지막 데이터", "~ 기준입니다" 등)도 있어야 한다. last_date를 모르면 None."""
    if last_date is None:
        return None
    answer = answer or ""
    mentioned = any(re.search(p, answer) for p in _date_patterns(last_date))
    stale = _business_days_between(last_date, today) >= STALE_BUSINESS_DAYS
    return mentioned and (not stale or bool(_STALE_NOTICE_RE.search(answer)))


# ---------- 도구 선택 / 기간 ----------


def tool_selection_ok(called: list[str], tools_all=None, tools_forbidden=None) -> bool:
    """tools_all이 모두 호출됐고 tools_forbidden은 하나도 호출되지 않았으면 True. tools_forbidden=['*']는 '도구 호출 없음'."""
    called_set = set(called)
    if not set(tools_all or []) <= called_set:
        return False
    forbidden = tools_forbidden or []
    if "*" in forbidden:
        return not called_set
    return not (called_set & set(forbidden))


PERIOD_DAYS_CAP = 60  # stock_tool/market_tool의 to_int(..., hi=60)


def period_matches(got_days, expected_days, cap: int = PERIOD_DAYS_CAP) -> bool:
    """도구가 실제로 쓰는 값(상한 cap 적용) 기준으로 비교한다. expected_days=None이면 '기간 없음'이 정답."""
    if expected_days is None:
        return got_days is None
    if got_days is None:
        return False
    return min(int(got_days), cap) == min(int(expected_days), cap)


# ---------- 숫자 부착 / 면책 문구 (2026-10-28 3차 라운드 추가, 기존 판정은 그대로) ----------


def attachment_errors(answer: str, per_company: dict[str, list], shared=()) -> list[str]:
    """여러 종목을 다룬 답변에서 숫자를 다른 종목에 붙인 경우. per_company: {종목명: 그 종목의 도구 결과 목록}.

    한 문장(또는 한 줄)에 종목명이 정확히 하나만 나오면, 그 문장의 값 숫자(원/%/주 또는 1000 이상)는 그 종목의 결과(+shared:
    질문 등)로 설명돼야 한다. 그 종목 결과로는 설명되지 않는데 다른 종목 결과로는 설명되면 "종목명: 숫자"로 돌려준다.
    두 종목을 함께 말한 문장(차이 비교)과 종목명이 없는 문장은 보지 않는다(한계). 어느 결과로도 설명되지 않는 숫자는
    number_support가 따로 센다."""
    errors = []
    names = sorted(per_company, key=len, reverse=True)
    for sentence in _SENTENCE_SPLIT_RE.split(answer or ""):
        hit, rest = [], sentence
        for n in names:  # 긴 이름 먼저: "KODEX 200"이 "KODEX 200TR" 안에서 두 번 세지지 않게
            if n in rest:
                hit.append(n)
                rest = rest.replace(n, " ")
        if len(hit) != 1:
            continue
        own = support_values(list(per_company[hit[0]]) + list(shared))
        others = support_values([r for k, v in per_company.items() if k != hit[0] for r in v])
        for num in extract_numbers(sentence):
            if _value_like(num) and not is_supported(num, own) and is_supported(num, others):
                errors.append(f"{hit[0]}: {num.raw}")
    return errors


_DISCLAIMER_RE = re.compile(r"투자\s*(?:권유|자문)[^.]{0,20}아니|투자\s*판단[^.]{0,40}책임")


def has_disclaimer(text: str) -> bool:
    """면책 문구(투자 권유가 아니라는 안내 또는 투자 판단의 책임 안내)가 있는지."""
    return bool(_DISCLAIMER_RE.search(text or ""))


# ---------- 집계 ----------


def rate(flags: list[bool]) -> float | None:
    return sum(1 for f in flags if f) / len(flags) if flags else None


def percentile(values: list[float], p: float) -> float | None:
    """선형 보간 백분위수(p는 0~100). 값이 없으면 None."""
    if not values:
        return None
    xs = sorted(values)
    k = (len(xs) - 1) * p / 100
    lo, hi = math.floor(k), math.ceil(k)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)
