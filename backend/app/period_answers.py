"""(2026-10-31) 과거 기간 시세 질문의 답을 코드가 만든다: 특정 날짜 종가, 휴장일 안내, 기간 등락률·최고/최저·거래량(시작·끝 날짜 명시).

배경: 3년치 시세 백필(2023-10-30~) 뒤 "기아 2024년 등락률"의 기간이 보유 범위 안이 되면서 app.period_scope의 코드 거절이 작동하지
않고 LLM + stock_tool(최근 N거래일만 조회)로 넘어갔다. 모델은 2026년 값에 "2024-10-30"이라는 날짜를 붙이거나("2024-10-30 종가 94,800원",
실제 2024-10-30 종가는 94,100원), 있는 데이터를 "없다"고 답했다. 그래서 날짜로 조회할 수 있는 시세 질문은 LLM에 맡기지 않는다.

이 모듈은 판별과 문장(순수 함수)만 담당하고, DB 조회는 app.agent._answer_period_history가 한다.
- 대상: 시세를 묻고(거래량 포함) 뉴스·원인·전망 같은 다른 정보를 묻지 않으며, 기간이 "하루"이거나 최근 창(파서가 N거래일로 답하는 경로)이
  아닌 질문. 그 밖에는 기존 흐름이다(애매하면 LLM 경로로 두고, 그 경로의 stock_tool에는 코드가 날짜 범위를 넘긴다).
- 보유 범위보다 앞서는 기간은 여기로 오기 전에 app.period_scope가 답한다(두 종목 질문은 여기서 같은 문장을 재사용한다).
"""

import re
from datetime import date

from app import code_answers, routing
from app.period_scope import PRICE_INTENT_RE, RequestedPeriod

# 이보다 짧은 과거 창("지난주", "지난달")은 기존 LLM 흐름으로 두고 stock_tool에 날짜 범위만 넘긴다(흐름 서술이 필요한 질문이 많다)
SHORT_WINDOW_DAYS = 60
ALL_TIME_LABEL = "보유 데이터 전체"
ALL_TIME_RE = re.compile(r"역대|사상\s*(?:최고|최저)|상장\s*(?:이후|이래)|전체\s*기간")
# 거래량은 이 경로가 답하므로 code_answers의 "다른 정보" 목록에서 뺀다
_OTHER_INTENT_RE = re.compile(code_answers._OTHER_INTENT_RE.pattern.replace("거래량|", ""), re.IGNORECASE)
_VOLUME_RE = re.compile(r"거래량")
_OHLC_RE = re.compile(r"시가(?!총액)|(?<!최)고가|(?<!최)저가")
_WEEKDAYS = "월화수목금토일"

REASON_WEEKEND = "weekend"
REASON_HOLIDAY = "holiday"
REASON_COMPANY = "company"
REASON_NOT_COLLECTED = "not_collected"


def is_history_question(question: str) -> bool:
    """시세만 묻는 질문인지. 다른 정보(뉴스·원인·전망 등)나 다른 도구가 필요한 질문은 False(기존 흐름)."""
    q = question or ""
    if not PRICE_INTENT_RE.search(q) or _OTHER_INTENT_RE.search(q):
        return False
    return not (routing.is_cause_question(q) or routing.needs_market_tool(q) or routing.needs_event_reaction_tool(q)
                or routing.needs_discovery_tool(q) or routing.explicitly_requested_tools(q) or routing.needs_fx_tool(q))


def is_market_period_question(question: str) -> bool:
    """시장 비교만 묻는 질문인지(뉴스·원인·전망 등을 함께 묻거나 다른 도구가 필요하면 False)."""
    q = question or ""
    return not (_OTHER_INTENT_RE.search(q) or routing.is_cause_question(q) or routing.needs_event_reaction_tool(q)
                or routing.needs_discovery_tool(q) or routing.needs_fx_tool(q))


def market_text(result: dict, requested: RequestedPeriod) -> str:
    """종목·지수 같은 구간 비교의 답: 요청 기간, market_tool 요약, 덮지 못한 범위 안내."""
    parts = [f"요청하신 기간({requested.label}: {requested.start.isoformat()}~{requested.end.isoformat()}) 기준입니다.", result["summary"]]
    if result.get("note"):
        parts.append(result["note"])
    return " ".join(parts)


def needs_date_lookup(requested: RequestedPeriod | None, parsed, today: date) -> bool:
    """최근 N거래일 창으로는 답할 수 없는 기간인지: 특정 하루(오늘 제외), 파서가 인식하지 못한 기간("2024년", "3분기"),
    끝이 오늘 이전인 기간("지난달"). 끝이 오늘인 창("최근 1년", "3개월 추이")은 기존 흐름이다(stock_tool이 전체 기간을 요약한다)."""
    if requested is None:
        return False
    if requested.start == requested.end:
        return requested.start != today
    if parsed is None:
        return True
    return parsed.end_date < today


def code_answers_period(requested: RequestedPeriod | None, parsed, today: date) -> bool:
    """날짜 조회가 필요한 기간 중 코드가 직접 답할 기간: 특정 하루, 파서가 모르는 기간, 긴 과거 창("작년")."""
    if not needs_date_lookup(requested, parsed, today):
        return False
    return requested.start == requested.end or parsed is None or parsed.period_days > SHORT_WINDOW_DAYS


def closed_reason(day: date, market_has_rows: bool, data_end: date) -> str:
    """이 종목 시세가 없는 날의 사유."""
    if day.weekday() >= 5:
        return REASON_WEEKEND
    if day > data_end:
        return REASON_NOT_COLLECTED
    return REASON_COMPANY if market_has_rows else REASON_HOLIDAY


def _day_label(day: date) -> str:
    return f"{day.isoformat()}({_WEEKDAYS[day.weekday()]})"


def closed_day_text(name: str, day: date, reason: str, previous: dict) -> str:
    """시세가 없는 날의 답. previous는 직전 거래일의 시세 {"price_date", "close_price"}다."""
    label = _day_label(day)
    close = f"{previous['close_price']:,.0f}원"
    if reason == REASON_NOT_COLLECTED:
        return (f"{label} {name} 시세 데이터가 없습니다(아직 수집되지 않았습니다). "
                f"마지막 데이터인 {previous['price_date']} 종가는 {close}입니다.")
    head = {
        REASON_WEEKEND: f"{label}은 주말이라 휴장이고 시세 데이터가 없습니다.",
        REASON_HOLIDAY: f"{label}은 휴장일이라 시세 데이터가 없습니다.",
        REASON_COMPANY: f"{label}은 다른 종목은 거래됐지만 {name} 시세 데이터가 없습니다(거래정지 등일 수 있습니다).",
    }[reason]
    return f"{head} 직전 거래일({previous['price_date']}) {name} 종가는 {close}입니다."


def day_text(name: str, row: dict, question: str = "") -> str:
    """거래일 하루의 답. row: price_date, close_price, change_pct, volume(시가·고가·저가가 있으면 질문이 물을 때 덧붙인다)."""
    text = f"{name}의 {row['price_date']} 종가는 {row['close_price']:,.0f}원입니다."
    extra = []
    if row.get("change_pct") is not None:
        extra.append(f"전일 대비 등락률은 {row['change_pct']:+.2f}%")
    if row.get("volume") is not None:
        extra.append(f"거래량은 {row['volume']:,}주")
    if extra:
        text += " " + ", ".join(extra) + "입니다."
    if _OHLC_RE.search(question or ""):
        ohlc = [f"{word} {row[key]:,.0f}원" for word, key in (("시가", "open_price"), ("고가", "high_price"), ("저가", "low_price"))
                if row.get(key) is not None]
        if ohlc:
            text += " " + ", ".join(ohlc) + "입니다."
    return text


def volume_stats(prices: list[dict]) -> dict:
    """기간 일평균·최대 거래량. 거래량이 없으면 빈 dict."""
    rows = [p for p in prices if p.get("volume") is not None]
    if not rows:
        return {}
    top = max(rows, key=lambda p: p["volume"])
    return {"volume_avg": round(sum(p["volume"] for p in rows) / len(rows)), "volume_max": top["volume"],
            "volume_max_date": top["price_date"]}


def window_text(result: dict, requested: RequestedPeriod, data_end: date, question: str = "") -> str:
    """기간 답: 요청 기간과 실제 시작·끝 날짜를 밝히고 코드가 계산한 요약을 쓴다."""
    q = question or ""
    if requested.label == ALL_TIME_LABEL:
        head = f"{ALL_TIME_LABEL}({requested.start.isoformat()}~{requested.end.isoformat()}) 기준입니다."
    else:
        head = f"요청하신 기간({requested.label}: {requested.start.isoformat()}~{requested.end.isoformat()}) 기준입니다."
    parts = [head]
    if _VOLUME_RE.search(q) and result.get("volume_avg") is not None:
        parts.append(
            f"{result['company_name']}의 이 기간 일평균 거래량은 {result['volume_avg']:,}주, "
            f"최대 거래량은 {result['volume_max']:,}주({result['volume_max_date']})입니다."
        )
    parts.append(result["summary"])
    if code_answers._HIGH_LOW_RE.search(q):
        parts.append("최고·최저는 종가 기준이며 장중 고가·저가가 아닙니다.")
    if requested.end > data_end:
        parts.append(f"마지막 데이터는 {data_end.isoformat()} 기준입니다.")
    return " ".join(parts)


def after_data_text(name: str, requested: RequestedPeriod, data_start: date, data_end: date) -> str:
    """요청 기간 전체가 보유 데이터 마지막 날보다 뒤일 때의 답."""
    return (f"요청하신 기간({requested.label}: {requested.start.isoformat()}~{requested.end.isoformat()})의 {name} 시세 데이터는 "
            f"아직 없습니다. 보유한 데이터는 {data_start.isoformat()}부터 {data_end.isoformat()}까지입니다.")
