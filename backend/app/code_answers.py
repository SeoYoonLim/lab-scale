"""(B, 2026-10-28) 코드가 숫자를 만드는 답변의 범위 확대: 기간 수익률, 기간 최고·최저가(날짜 포함), 두 종목 비교.

배경: 단일 종목 현재가·등락률·거래량(app/answer_guard.is_simple_quote)만 코드가 답하고, "3개월 수익률", "한 달 최고가",
"A와 B 중 어디가 더 올랐어"는 LLM이 도구 결과를 옮겨 적었다. 이런 질문은 숫자가 핵심이라 옮겨 적다 틀리면(자릿수, 다른 종목,
다른 기간) 그대로 오답이다. 그래서 유형이 분명한 경우만 코드 템플릿으로 답하고, 애매하면 기존 LLM 경로로 둔다.

판별(classify): 질문에서 기간을 인식했고(app.period_parser.parse_period), 기존 라우팅 게이트가 다른 의도(원인·흐름·시장 비교·
사건 반응·시장 탐색·환율·도구 이름 요청)로 보지 않고, 뉴스·전망·실적·거래량 같은 다른 정보를 묻지 않을 때만:
- 종목 1개 + 최고가/최저가/가장 비쌌던 → high_low
- 종목 1개 + 수익률/등락률/얼마나 올랐 → period_return
- 종목 2개 + 비교/중 어디/더 올랐/각각 또는 수익률·최고가 → compare (두 종목의 같은 기간 창이 같은 날짜일 때만)
시세 창: stock_tool과 같은 정의(최근 N거래일 + 구간 직전 종가를 기준, app.tools.stock_tool.summarize_period).
장중 규칙: 평일 15:30(KST) 전에는 오늘 행(장중 값)을 쓰지 않는다(app.briefing.drop_open_session, 브리핑과 같은 규칙).
"""

import re
from datetime import date, datetime

from app import routing
from app.briefing import drop_open_session
from app.korean import josa
from app.tools.stock_tool import summarize_period

KIND_RETURN = "period_return"
KIND_HIGH_LOW = "high_low"
KIND_COMPARE = "compare"

_RETURN_RE = re.compile(r"수익률|등락률|상승률|하락률|얼마나\s*(?:올|내|떨어|빠)|몇\s*(?:%|퍼센트|프로)|올랐|내렸|떨어졌|빠졌")
_HIGH_LOW_RE = re.compile(r"최고가|최저가|최고점|최저점|고점|저점|가장\s*(?:비쌌|쌌|높았|낮았)|제일\s*(?:비쌌|쌌|높았|낮았)")
_COMPARE_RE = re.compile(r"비교|중\s*(?:어디|어느|누가|뭐가|어떤)|어느\s*쪽|더\s*(?:많이\s*)?(?:올|내|떨어|빠|높|낮)|vs|각각")
# 숫자 템플릿이 답하지 않는 다른 정보를 함께 묻는 질문은 LLM 경로다(애매하면 확대하지 않는다)
_OTHER_INTENT_RE = re.compile(
    r"뉴스|공시|기사|왜|이유|원인|전망|분석|평가|실적|매출|이익|PER|PBR|ROE|배당|구성|비중|반응|이후|직후|"
    r"사도|살까|팔까|사야|팔아|투자|위험|리스크|거래량|거래대금|시가총액|외국인|기관|설명|의미|차이점|특징",
    re.IGNORECASE,
)


def classify(question: str, n_companies: int, has_period: bool) -> str | None:
    q = question or ""
    if not has_period or n_companies not in (1, 2) or _OTHER_INTENT_RE.search(q):
        return None
    if (routing.is_cause_question(q) or routing.is_trend_question(q) or routing.needs_market_tool(q)
            or routing.needs_event_reaction_tool(q) or routing.needs_discovery_tool(q)
            or routing.explicitly_requested_tools(q) or routing.needs_fx_tool(q)):
        return None
    if n_companies == 2:
        return KIND_COMPARE if (_COMPARE_RE.search(q) or _RETURN_RE.search(q) or _HIGH_LOW_RE.search(q)) else None
    if _HIGH_LOW_RE.search(q):
        return KIND_HIGH_LOW
    if _RETURN_RE.search(q):
        return KIND_RETURN
    return None


def _price_dict(row) -> dict:
    return {
        "price_date": row.price_date.isoformat(),
        "close_price": float(row.close_price),
        "volume": row.volume,
        "change_pct": float(row.change_pct) if row.change_pct is not None else None,
    }


def window_result(company, rows_desc: list, start: date, now: datetime, called_via: str) -> dict | None:
    """최신순 시세 행으로 stock_tool 결과와 같은 모양의 dict를 만든다(평가·검증·출처가 stock_tool 결과처럼 읽는다).

    창은 날짜 기준이다: start(질문 기간의 시작일, parse_period) 이후의 행을 쓰고, start 직전 거래일 종가를 기준으로 삼는다.
    행 수(N거래일) 기준이면 연휴로 행이 없는 날만큼 창이 앞으로 밀린다("한 달"이 9월 중순부터가 됨). rows_desc에는 기준 종가가
    들어갈 만큼 여유 있게(start 이전 행 포함) 넘긴다. 창이 2거래일이 안 되거나 기준 종가가 없으면 None."""
    rows = drop_open_session(list(rows_desc), now, lambda r: r.price_date)
    inside = [r for r in rows if r.price_date >= start]
    before = [r for r in rows if r.price_date < start]
    base_row = before[0] if before else None
    prices = [_price_dict(r) for r in reversed(inside)]
    if not prices or base_row is None:
        return None
    period_days = len(prices)
    base = {"price_date": base_row.price_date.isoformat(), "close_price": float(base_row.close_price)} if base_row else None
    summary = summarize_period(company.name, prices, base)
    if not summary:
        return None
    latest = prices[-1]
    return {
        "ticker": company.ticker,
        "company_name": company.name,
        "period_days": period_days,
        "found": True,
        "change_pct": latest["change_pct"],
        "volume": latest["volume"],
        **summary,
        "prices": prices,
        "called_via": called_via,
    }


_PERIOD_KEYS = ("summary", "period_change_pct", "period_start_date", "period_end_date", "trading_days")


def latest_of(result, now: datetime):
    """stock_tool(period_days=2) 결과를 단순 시세용 1일 결과로 줄인다. 장중(평일 15:30 전)이면 오늘 행(장중 값) 대신
    직전 거래일 행이 마지막 값이다. 결과가 dict가 아니거나 prices가 없으면 그대로 돌려준다(호출측의 quote_answer가 판단)."""
    if not isinstance(result, dict) or not result.get("found") or not result.get("prices"):
        return result
    prices = drop_open_session(result["prices"], now, lambda p: date.fromisoformat(str(p["price_date"])[:10]))
    if not prices:
        return None
    latest = prices[-1]
    out = {k: v for k, v in result.items() if k not in _PERIOD_KEYS}
    out.update(period_days=1, change_pct=latest.get("change_pct"), volume=latest.get("volume"), prices=[latest])
    return out


def single_answer(kind: str, result: dict) -> str:
    """종목 하나: 기간 등락률과 기간 종가 최고·최저(날짜 포함)는 summarize_period의 문장 그대로다."""
    text = result["summary"]
    if kind == KIND_HIGH_LOW:
        text += " 최고·최저는 종가 기준이며 장중 고가·저가가 아닙니다."
    return text


def compare(a: dict, b: dict) -> tuple[str, float] | None:
    """두 종목의 같은 기간 비교 문장과 등락률 차이(%p). 두 창의 시작·끝 날짜가 다르면 None(LLM 경로로 둔다)."""
    if (a["period_start_date"], a["period_end_date"]) != (b["period_start_date"], b["period_end_date"]):
        return None
    pa, pb = a["period_change_pct"], b["period_change_pct"]
    diff = round(pa - pb, 2)
    head = f"{a['period_start_date']} 종가 대비 {a['period_end_date']}까지 {a['trading_days']}거래일 기준 비교입니다(종가 기준)."
    if diff == 0:
        last = f"같은 기간 {josa(a['company_name'], '와/과')} {b['company_name']}의 등락률은 같습니다."
    else:
        hi, lo = (a, b) if diff > 0 else (b, a)
        last = f"같은 기간 등락률은 {josa(hi['company_name'], '이/가')} {lo['company_name']}보다 {abs(diff):.2f}%p 높았습니다."
    return "\n\n".join([head, a["summary"], b["summary"], last]), abs(diff)
