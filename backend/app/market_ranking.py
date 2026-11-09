"""시장 전체 거래량 순위 질문(2026-11-07 35차): "거래량 많은 종목", "거래량 상위 10개", "코스피 거래량 상위", "거래량 급증 종목".

33차: "오늘 거래량 많은 종목 알려줘"는 routing.needs_discovery_tool이 '급증·증가' 단어가 있어야 탐색으로 봐서 LLM 일반 경로로 갔다.
저장된 stock_price(최근 거래일 거래량, company.market)만으로 계산할 수 있으므로 LLM 없이 코드가 답한다(외부 호출 없음).

- 거래량 상위(volume_top): 최근 거래일 거래량(주) 순. 거래량 급증(volume_surge): 당일 거래량 / 직전 20거래일 평균 >= 2배, 배수 순.
  둘은 다르다(대형주는 평소에도 거래량이 많다). 질문에 '급증·늘어·폭증'이 있으면 급증, 아니면 상위.
- 시장: 질문에 코스피/코스닥이 있으면 그 시장만(company.market), 없으면 전체 주식(ETF 제외).
- 기준일: 최근 거래일 종가 기준이고 실시간 순위가 아니다. 기준일·지연 안내는 app.quote_freshness(단순 시세와 같은 문장).
"""

from __future__ import annotations

import re
from datetime import date, datetime

from app import quote_freshness

_VOLUME_RE = re.compile(r"거래량|거래\s*많")
_TOP_RE = re.compile(r"많은|많이|상위|순위|top|톱|1위|가장\s*많|제일\s*많|활발", re.I)
_SURGE_RE = re.compile(r"급증|급등|늘어|늘었|폭증|증가|터진|폭발")
_LIMIT_RE = re.compile(r"(?:상위|top|톱)\s*(\d{1,2})|(\d{1,2})\s*(?:개|종목|위)", re.I)
_MARKET_RE = (("KOSPI", re.compile(r"코스피|kospi|유가증권", re.I)), ("KOSDAQ", re.compile(r"코스닥|kosdaq", re.I)))
DEFAULT_LIMIT, MAX_LIMIT = 10, 30


def kind_of(question: str) -> str | None:
    """'volume_top' | 'volume_surge' | None. 종목을 특정한 질문인지는 호출측이 본다(종목이 있으면 이 경로가 아니다)."""
    q = question or ""
    if not _VOLUME_RE.search(q) or not re.search(r"종목|주식|어디|뭐|어떤|알려|보여|순위|상위", q):
        return None
    if _SURGE_RE.search(q):
        return "volume_surge"
    if _TOP_RE.search(q):
        return "volume_top"
    return None


def limit_of(question: str) -> int:
    m = _LIMIT_RE.search(question or "")
    n = int(next(g for g in m.groups() if g)) if m else DEFAULT_LIMIT
    return max(1, min(MAX_LIMIT, n))


def market_of(question: str) -> str | None:
    return next((code for code, rx in _MARKET_RE if rx.search(question or "")), None)


_MARKET_NAME = {"KOSPI": "코스피", "KOSDAQ": "코스닥", None: "전체(코스피·코스닥 주식, ETF 제외)"}


def answer(result: dict, kind: str, market: str | None, now: datetime) -> str:
    """get_trending 결과로 만든 답. found가 아니거나 비면 데이터가 없다고 답한다(지어내지 않는다)."""
    scope = _MARKET_NAME[market]
    if not result.get("found"):
        return f"거래량 순위를 계산할 시세 데이터가 없습니다({result.get('message') or '데이터 없음'})."
    base = date.fromisoformat(result["price_date"])
    items = result.get("items") or []
    if kind == "volume_top":
        head = f"## 거래량 상위 {len(items)}개 종목 · {scope} · {result['price_date']} 종가 기준"
        lines = [f"{i}. {it['company_name']}({it['ticker']}) {it['volume']:,}주"
                 + (f", 등락률 {it['change_pct']:+.2f}%" if it.get("change_pct") is not None else "")
                 for i, it in enumerate(items, 1)]
        note = ("- 거래량 상위는 그날 거래된 주식 수가 많은 순서입니다. 평소보다 거래가 갑자기 늘어난 종목(거래량 급증)과는 다릅니다"
                " - 급증은 \"거래량 급증 종목 알려줘\"로 물어보세요.")
    else:
        head = (f"## 거래량 급증 종목 · {scope} · {result['price_date']} 종가 기준"
                f"(직전 {result.get('window_days')}거래일 평균 대비 2배 이상)")
        lines = [f"{i}. {it['company_name']}({it['ticker']}) {it['volume']:,}주, 평균 {round(it['avg_volume']):,}주의 {it['volume_ratio']:.2f}배"
                 for i, it in enumerate(items, 1)]
        if not lines:
            lines = ["- 평균 대비 2배 이상 늘어난 종목이 없습니다."]
        note = "- 거래량 급증은 평소(직전 거래일 평균)보다 거래가 늘어난 정도이고, 거래량이 가장 많은 종목 순서와는 다릅니다."
    return "\n".join([head, "", *lines, "", note, f"- {quote_freshness.line(base, now)}",
                      "- 순위는 등락률·거래량 수치만으로 정렬한 것이며 매수·매도 추천이 아닙니다."])
