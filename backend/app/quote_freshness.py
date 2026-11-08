"""저장된 종가 답(단순 시세·주문 안내의 참고 시세·시장 순위)에 붙이는 기준일·지연 안내(2026-11-07 35차).

최신성 계산은 새로 만들지 않는다: 기대 거래일은 app.outlook.expected_trading_day(평일, 15:30 전이면 직전 평일),
지연 영업일은 app.data_status.business_days_behind(평일 기준, 공휴일 달력 없음)로 주가 흐름·전망 답과 같다.
이 답의 값은 실시간 시세가 아니라 DB에 저장된 종가라는 것을 항상 밝힌다(실시간 시세는 GET /api/stocks/{ticker}/realtime-price).

문장은 언어별 템플릿이다. 한국어 답을 번역하지 않고 같은 값으로 바로 만든다(app.quote_i18n).

늦은 이유(2026-11-08): 휴장일 달력이 없어 공휴일 다음 날에도 평일 기준으로 지연을 센다(예: 2026-10-09 한글날 뒤 월요일 장중에는 10-08 종가가
최신인데 "1영업일 이전"). 전망 답과 같은 추정(app.outlook.price_status: 미수집·수집 실패·휴장일 추정·종목만 누락, 수집 기록으로 추정)을 받으면
이유를 한 문장 붙인다. 지연 영업일 수 자체는 바꾸지 않는다(전망·주가 흐름·GET /api/status와 같은 수).
"""

from __future__ import annotations

from datetime import date, datetime

from app.data_status import business_days_behind

LOCALES = ("ko-KR", "en-US", "ja-JP", "zh-CN")

_FRESH = {
    "ko-KR": "데이터 기준일: {base} 종가(DB에 저장된 종가이며 실시간 시세가 아닙니다).",
    "en-US": "Data as of: {base} close (a stored closing price, not a real-time quote).",
    "ja-JP": "データ基準日: {base} 終値（DBに保存された終値で、リアルタイムの株価ではありません）。",
    "zh-CN": "数据基准日：{base} 收盘价（数据库中保存的收盘价，并非实时行情）。",
}
_LAG1 = {
    "ko-KR": "마지막 시세({base})가 기대 거래일({exp})보다 1영업일 이전 데이터입니다.",
    "en-US": "The last price ({base}) is 1 business day older than the expected trading day ({exp}).",
    "ja-JP": "最後の株価（{base}）は想定取引日（{exp}）より1営業日前のデータです。",
    "zh-CN": "最后的股价（{base}）比预期交易日（{exp}）早1个工作日。",
}
_STALE = {
    "ko-KR": "마지막 시세({base})가 기대 거래일({exp})보다 {lag}영업일 지연된 데이터라 최신 시세가 아닙니다.",
    "en-US": "The last price ({base}) is {lag} business days behind the expected trading day ({exp}), so it is not the latest price.",
    "ja-JP": "最後の株価（{base}）は想定取引日（{exp}）より{lag}営業日遅れたデータのため、最新の株価ではありません。",
    "zh-CN": "最后的股价（{base}）比预期交易日（{exp}）滞后{lag}个工作日，并非最新股价。",
}

_REASON = {
    "ko-KR": "늦은 이유(수집 기록으로 추정): {label}({detail}).",
    "en-US": "Likely reason (inferred from collection logs): {label}.",
    "ja-JP": "遅れの理由（収集記録から推定）：{label}。",
    "zh-CN": "滞后原因（根据采集记录推定）：{label}。",
}


def reason_text(price_status: dict | None, locale: str = "ko-KR") -> str | None:
    """app.outlook.price_status 결과의 이유 한 문장(최신이면 None). 한국어 외에는 세부 기록(한국어)을 빼고 라벨만 옮긴다."""
    from app import outlook as o

    st = (price_status or {}).get("status")
    if not st or st == o.PRICE_STATUS_LATEST:
        return None
    L = locale if locale in LOCALES else "ko-KR"
    if L == "ko-KR":
        return _REASON[L].format(label=st, detail=price_status.get("detail") or "")
    from app.outlook_i18n import _PRICE_STATUS

    label = _PRICE_STATUS.get(st, {}).get(L)
    return _REASON[L].format(label=label) if label else None


def status(base: date, now: datetime) -> dict:
    """{"base", "expected", "lag"}: 기대 거래일과 지연 영업일(기존 계산 재사용)."""
    from app.outlook import expected_trading_day

    exp = expected_trading_day(now)
    return {"base": base, "expected": exp, "lag": business_days_behind(base, exp)}


def line(base: date, now: datetime, locale: str = "ko-KR", price_status: dict | None = None) -> str:
    """기준일·저장 종가 안내 한 줄 + 지연이면 지연 문장(1영업일은 정보, 2영업일 이상은 최신이 아니라고 밝힌다)
    + price_status(app.outlook.price_status 결과)가 있고 지연이면 늦은 이유."""
    L = locale if locale in LOCALES else "ko-KR"
    s = status(base, now)
    out = _FRESH[L].format(base=s["base"].isoformat())
    if s["lag"] == 1:
        out += " " + _LAG1[L].format(base=s["base"].isoformat(), exp=s["expected"].isoformat())
    elif s["lag"] >= 2:
        out += " " + _STALE[L].format(base=s["base"].isoformat(), exp=s["expected"].isoformat(), lag=s["lag"])
    if s["lag"] >= 1 and (why := reason_text(price_status, L)):
        out += " " + why
    return out


def base_of(result: dict) -> date | None:
    """stock_tool 결과의 마지막 시세 날짜."""
    if not isinstance(result, dict) or not result.get("found"):
        return None
    prices = result.get("prices") or []
    d = (prices[-1].get("price_date") if prices else None) or result.get("period_end_date") or result.get("price_date")
    return date.fromisoformat(str(d)[:10]) if d else None
