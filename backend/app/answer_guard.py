"""최종 답변의 숫자 검증과 코드 요약(서비스용).

배경: 평가(backend/evals)에서 "NAVER 주가 얼마야?"에 1,792,000원(실제 179,200원), 기아 거래량에 "전일보다 794,309주 증가"
(도구 결과에 없는 값)가 그대로 나갔다. 원칙: 시세·기간·등락률 같은 숫자는 코드가 만든 값만 사용자에게 나간다.

- guard_answer: LLM 답변에서 숫자를 뽑아 도구 결과·질문·도구 결과의 단순 계산(두 시점 값의 차이와 변화율)으로 설명되는지 본다.
  하나라도 설명되지 않으면 그 답변을 내보내지 않고 도구 결과로 코드가 만든 요약(code_summary)으로 바꾼다.
  도구 결과가 없으면(도구 없이 답한 경우) 설명되지 않는 값 숫자(원·%·주)가 든 문장만 지운다.
- 뉴스·공시 같은 정성 서술은 숫자만 검증하고 내용(사실 여부)은 검증하지 않는다.
- 숫자 추출·제외 규칙은 평가기(evals/metrics.py)와 같은 기준이다. 서비스 코드가 평가기를 import하지 않도록 따로 두고,
  두 구현의 추출 결과가 같은지는 tests/test_answer_guard.py의 동기화 테스트가 확인한다.
"""

import math
import re
from dataclasses import dataclass
from itertools import combinations

# ---------- 숫자 추출 (evals/metrics.py와 같은 규칙) ----------

_UNIT_SCALE = {"조": 1e12, "억": 1e8, "만": 1e4, "천": 1e3}

_EXCLUDE_PATTERNS = [
    r"\d{4}\s*[-./]\s*\d{1,2}\s*[-./]\s*\d{1,2}\.?",
    r"\d{2,4}\s*년\s*\d{1,2}\s*월(?:\s*\d{1,2}\s*일)?",
    r"\d{1,2}\s*월\s*\d{1,2}\s*일",
    r"(?<!\d)(?:19|20)\d{2}\s*년(?:도)?",
    r"(?<![\d,.])\d{1,2}\s*월(?!요일|간|별)",
    r"(?<![\d,.])\d{1,2}/\d{1,2}(?![\d/])",
    r"\d{1,2}:\d{2}(?::\d{2})?",
    r"^\s*[-*]?\s*\(?\d{1,2}[.)](?!\d)",
    r"(?<!\d)\d{6}(?!\d)",
    r"(?<![\d,.])(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?\s*(?:거래일|영업일|일간|일|개월|달(?!러)|년간|년|주간|주일|주\s*동안|주\s*연속"
    r"|분기|시간|시(?![가-힣])|시에|분|초|위|번째|차|회|건|개|곳|명|가지|종목|단계)",
]
_EXCLUDE_RE = re.compile("|".join(f"(?:{p})" for p in _EXCLUDE_PATTERNS), re.M)
_NUM_RE = re.compile(
    r"(?<![\w&.,])([+\-−]?)(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d+))?"
    r"(?:\s*(조|억|만|천)(?=[원주]|[^가-힣]|$))?"
    r"\s*(%p|%|퍼센트|원|주|배|포인트|p)?"
)
_COMPOUND_RE = re.compile(
    r"(?<![\w&.,])((?:\d{1,3}(?:,\d{3})+|\d+)\s*(?:조|억|만)\s*){1,2}"
    r"(\d{1,3}(?:,\d{3})+|\d+)?\s*(?:(조|억|만)\s*)?(원|주)"
)
_COMPOUND_PART_RE = re.compile(r"(\d{1,3}(?:,\d{3})+|\d+)\s*(조|억|만)?")


@dataclass(frozen=True)
class Number:
    value: float
    raw: str
    tolerance: float
    unit: str


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


def _compound(m: re.Match) -> Number | None:
    parts = [(float(n.replace(",", "")), _UNIT_SCALE.get(u or "", 1.0)) for n, u in _COMPOUND_PART_RE.findall(m.group(0))]
    scales = [s for _, s in parts]
    if len(parts) < 2 or scales != sorted(scales, reverse=True) or len(set(scales)) != len(scales):
        return None
    return Number(value=sum(v * s for v, s in parts), raw=m.group(0).strip(), tolerance=scales[-1], unit=m.group(4))


def extract_numbers(text: str, apply_exclusions: bool = True) -> list[Number]:
    """답변에서 숫자를 뽑는다. apply_exclusions=True면 날짜·시각·목록 번호·종목코드·기간/횟수 표현은 뺀다."""
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


# ---------- 근거 값 ----------

# 단순 계산을 허용할 시계열 값(같은 결과 안의 두 시점 사이 차이·변화율)
_SERIES_KEYS = ("close_price", "volume", "close_rate", "close")
MAX_SERIES = 80


def support_values(obj) -> list[float]:
    """도구 결과(dict/list/str/숫자)의 모든 숫자 값(절대값). 문자열 속 숫자도 포함한다."""
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


def derived_values(results) -> list[float]:
    """도구 결과의 시계열(prices 등)에서 두 시점 값의 차이와 변화율(%). 등락률·증감을 계산해서 말한 답변을 허용하기 위해서다."""
    out: list[float] = []

    def series_in(o):
        if isinstance(o, dict):
            for v in o.values():
                yield from series_in(v)
        elif isinstance(o, list) and o and all(isinstance(x, dict) for x in o):
            for key in _SERIES_KEYS:
                vals = [x[key] for x in o if isinstance(x.get(key), (int, float)) and not isinstance(x.get(key), bool)]
                if len(vals) >= 2:
                    yield vals[-MAX_SERIES:]
            for x in o:
                yield from series_in(x)

    for series in series_in(results if isinstance(results, (list, dict)) else []):
        for a, b in combinations(series, 2):
            out.append(abs(b - a))
            if a:
                out.append(abs((b / a - 1) * 100))
    return out


def is_supported(n: Number, supports: list[float]) -> bool:
    tol = max(n.tolerance, n.value * 0.005)
    return any(abs(n.value - v) <= tol + 1e-9 for v in supports)


def unsupported_numbers(answer: str, results: list, question: str = "") -> list[Number]:
    supports = support_values(results) + support_values(question) + derived_values(results)
    return [n for n in extract_numbers(answer) if not is_supported(n, supports)]


# ---------- 코드 요약 ----------


def _price_sentence(r: dict) -> str | None:
    if r.get("summary"):
        return r["summary"]
    prices = r.get("prices") or []
    if not prices:
        return None
    last = prices[-1]
    parts = [f"{r.get('company_name')}의 {last['price_date']} 종가는 {last['close_price']:,.0f}원입니다."]
    extra = []
    if last.get("change_pct") is not None:
        extra.append(f"전일 대비 등락률은 {last['change_pct']:+.2f}%")
    if last.get("volume") is not None:
        extra.append(f"거래량은 {last['volume']:,}주")
    if extra:
        parts.append(", ".join(extra) + "입니다.")
    return " ".join(parts)


def _titles(items: list, key_url: str = "url", limit: int = 5) -> list[str]:
    lines = []
    for it in items[:limit]:
        date = it.get("published_at") or it.get("rcept_dt") or it.get("disclosed_at") or ""
        date = str(date)[:10]
        lines.append(f"- {it.get('title')}" + (f" ({date})" if date else ""))
    return lines


def code_summary(records: list[dict]) -> str | None:
    """도구 결과로 코드가 만든 요약. 쓸 수 있는 결과가 하나도 없으면 None."""
    prices, market, others, news, disclosures = [], [], [], [], []
    for rec in records:
        r = rec.get("result")
        if not isinstance(r, dict) or not r.get("found"):
            continue
        name = rec.get("tool_name")
        if name == "stock_tool" and (s := _price_sentence(r)):
            prices.append(s)
        elif name == "market_tool" and r.get("summary"):
            market.append(r["summary"])
        elif name == "fx_tool" and r.get("latest_rate") is not None:
            text = f"{r.get('latest_date')} 원/달러 환율은 {r['latest_rate']:,.2f}원입니다."
            if r.get("daily_change_pct") is not None:
                text += f" 전일 대비 {r['daily_change_pct']:+.2f}%입니다."
            others.append(text)
        elif name == "discovery_tool" and r.get("items"):
            items = [f"{it.get('company_name') or it.get('name')}({it['change_pct']:+.2f}%)"
                     for it in r["items"][:5] if it.get("change_pct") is not None]
            if items:
                others.append(f"{r.get('price_date')} 기준: " + ", ".join(items))
        elif name == "news_tool" and r.get("news"):
            news.extend(_titles(r["news"]))
        elif name == "disclosure_tool" and r.get("disclosures"):
            disclosures.extend(_titles(r["disclosures"]))
        elif name == "rag_search_tool" and r.get("results"):
            news.extend(_titles(r["results"]))
    sections = []
    if prices or market:
        sections.append("## 주가 동향\n" + "\n".join(prices + market))
    if others:
        sections.append("\n".join(others))
    if news or disclosures:
        sections.append("## 뉴스·공시 근거\n" + "\n".join(news + disclosures))
    return "\n\n".join(sections) if sections else None


_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+|\n")


def guard_answer(answer: str, records: list[dict], question: str = "") -> tuple[str, dict]:
    """(내보낼 답변, 검증 기록). 기록: {"replaced": bool, "violations": [원문 숫자], "mode": "code_summary"|"drop_sentences"|None}."""
    results = [r.get("result") for r in records]
    bad = unsupported_numbers(answer, results, question)
    log = {"replaced": False, "violations": [n.raw for n in bad], "mode": None}
    if not bad:
        return answer, log
    if records:
        summary = code_summary(records)
        if summary:
            log.update(replaced=True, mode="code_summary")
            return summary, log
    # 도구 결과가 없거나 요약할 결과가 없으면: 설명되지 않는 값 숫자가 든 문장만 지운다
    bad_raw = {n.raw for n in bad if n.unit in ("원", "%", "주") or n.value >= 1000}
    if not bad_raw:
        return answer, log
    kept = []
    for line in answer.split("\n"):
        sentences = [s for s in _SENTENCE_SPLIT_RE.split(line) if s]
        sentences = [s for s in sentences if not any(n.raw in bad_raw for n in extract_numbers(s))]
        if sentences or not line.strip():
            kept.append(" ".join(sentences))
    log.update(replaced=True, mode="drop_sentences")
    return "\n".join(kept).strip(), log


# ---------- 단순 시세 질문 ----------

_QUOTE_RE = re.compile(r"주가|가격|시세|현재가|종가|얼마|등락률|등락|거래량|시가|고가|저가")
# 이런 말이 있으면 단순 시세 질문이 아니다(기존 흐름: 원인·흐름·비교·뉴스 등)
_NOT_QUOTE_RE = re.compile(
    r"뉴스|공시|왜|이유|원인|전망|분석|비교|대비|코스피|코스닥|지수|흐름|추이|추세|동향|움직|수익률|어때|어떻|상황|"
    r"사도|살까|팔까|팔아|사야|투자|배당|실적|매출|이익|PER|PBR|ROE|구성|비중|반응|이후|직후|올랐|내렸|떨어|오른|내린|"
    r"급등|급락|상승|하락|빠졌|빠진|빠지|빠져|오르|내리|약해|강해|장\s*전체|시장|최고|최저|평균|기간|동안|까지|부터|\?.+\?",
    re.IGNORECASE,
)
# "오늘/지금/현재/최근"은 단순 시세 질문에 흔히 붙는다(기간이 아니라 '가장 최근 값'을 묻는 말)
_NOW_OK_RE = re.compile(r"오늘|지금|현재|최근|요즘|요새|금일")


def is_simple_quote(question: str, mentions_period: bool, period_text: str | None) -> bool:
    """단일 종목의 현재가·등락률·거래량만 묻는 질문인지(종목 개수는 호출측이 본다).
    mentions_period/period_text는 app.period_parser의 판정(기간을 말했는지, 인식한 표현)이다."""
    from app import routing

    q = question or ""
    if not _QUOTE_RE.search(q) or _NOT_QUOTE_RE.search(q):
        return False
    # 기존 라우팅 게이트가 다른 의도로 보는 질문(원인·흐름·시장 비교·사건 반응·시장 탐색·여러 정보 요청)은 단순 시세가 아니다
    if (routing.is_cause_question(q) or routing.is_trend_question(q) or routing.needs_market_tool(q)
            or routing.needs_event_reaction_tool(q) or routing.needs_discovery_tool(q)
            or routing.explicitly_requested_tools(q) or routing.needs_fx_tool(q)):
        return False
    if mentions_period and not (period_text in ("오늘",) or (period_text is None and _NOW_OK_RE.search(q))):
        return False
    return True


def quote_answer(result: dict) -> str | None:
    """stock_tool(period_days=1) 결과로 만든 단순 시세 답변."""
    if not isinstance(result, dict) or not result.get("found"):
        return None
    return _price_sentence({k: v for k, v in result.items() if k != "summary"})


# ---------- 데이터 기준일 표시 ----------
# "오늘/지금/현재/최근" 같은 현재 시점 표현이 있는 시세 질문에서, 답에 쓴 마지막 시세 날짜가 최근 거래일보다 영업일 기준
# STALE_BUSINESS_DAYS(app/data_status.py, 2일) 이상 오래됐으면 "마지막 데이터는 YYYY-MM-DD 기준입니다."를 코드가 붙인다.
# 판정은 GET /api/status와 같은 app.data_status.is_stale을 쓴다. 기간을 명시한 질문("최근 3개월")은 대상이 아니다.
_NOW_RE = re.compile(r"오늘|지금|현재|최근|요즘|요새|금일|현시점")
_PRICE_INTENT_RE = re.compile(r"주가|가격|시세|현재가|종가|얼마|등락|거래량")


def latest_price_date(records: list[dict]):
    """stock_tool 결과들의 마지막 시세 날짜 중 가장 이른 것(여러 종목이면 가장 오래된 쪽을 알린다). 없으면 None."""
    from datetime import date

    dates = []
    for rec in records:
        r = rec.get("result")
        if rec.get("tool_name") == "stock_tool" and isinstance(r, dict) and r.get("found"):
            prices = r.get("prices") or []
            d = (prices[-1].get("price_date") if prices else None) or r.get("period_end_date")
            if d:
                dates.append(date.fromisoformat(str(d)[:10]))
    return min(dates) if dates else None


def freshness_note(question: str, records: list[dict], today, period_text: str | None) -> str | None:
    """현재 시점 시세 질문인데 마지막 데이터가 오래됐으면 붙일 문장, 아니면 None. period_text는 parse_period가 인식한 표현."""
    from app.data_status import is_stale

    q = question or ""
    if not _NOW_RE.search(q) or not _PRICE_INTENT_RE.search(q) or period_text not in (None, "오늘"):
        return None
    latest = latest_price_date(records)
    if latest is None or not is_stale(latest, today):
        return None
    return f"마지막 데이터는 {latest.isoformat()} 기준입니다."
