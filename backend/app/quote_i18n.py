"""언어별 시세 문장과 번역 실패 폴백(2026-11-07 35차).

1. 단순 시세 답(app.agent._answer_simple_quote)은 번역하지 않고 같은 도구 결과로 언어별 템플릿을 바로 쓴다. 기록으로 다시 만든
   한국어 답이 저장된 답과 글자까지 같을 때만 쓴다(outlook_i18n·price_flow_i18n과 같은 조건). 아니면 None -> 보호 번역.
2. 보호 번역(app.i18n_translate)이 검증에 실패하면 예전에는 "안내 + 한국어 원문"이었다(33차 live: 일본어·영어 화면에 한국어 답).
   이제 그 언어의 고정 안내 + 도구 결과로 만든 데이터 요약(종가·등락률·거래량·기준일·지연, 거래량 순위, 근거 기사·공시 제목) + 면책이다.
   숫자·날짜·종목코드는 도구 결과 값 그대로이고 표기만 언어 규칙(daily_briefing_text.won/pct)을 따른다. 종목명·기사/공시 제목은
   기존 정책대로 원문이다. LLM이 쓴 해석 문장은 옮기지 못했다고 밝힌다(계산 실패가 아니다).
"""

from __future__ import annotations

from datetime import date, datetime

from app import daily_briefing_text as dbt, quote_freshness
from app.i18n import disclaimer, text

LOCALES = ("en-US", "ja-JP", "zh-CN")

_PRICE = {
    "en-US": "{name}: close {close} on {date}.",
    "ja-JP": "{name}の{date}終値は{close}です。",
    "zh-CN": "{name}{date}收盘价为{close}。",
}
_CHANGE = {"en-US": "Daily change {pct}", "ja-JP": "前日比{pct}", "zh-CN": "较前一日{pct}"}
_VOLUME = {"en-US": "volume {vol} shares", "ja-JP": "出来高{vol}株", "zh-CN": "成交量{vol}股"}
_JOIN = {"en-US": ", ", "ja-JP": "、", "zh-CN": "，"}
_END = {"en-US": ".", "ja-JP": "です。", "zh-CN": "。"}
_RANK_HEAD = {
    "volume_top": {"en-US": "Top stocks by trading volume ({date} close):", "ja-JP": "出来高上位銘柄（{date}終値基準）：",
                   "zh-CN": "成交量居前的股票（{date}收盘价基准）："},
    "volume_surge": {"en-US": "Stocks with surging volume vs. the previous average ({date} close):",
                     "ja-JP": "出来高が直前平均より急増した銘柄（{date}終値基準）：", "zh-CN": "成交量较此前平均急增的股票（{date}收盘价基准）："},
}
_RANK_ITEM = {"en-US": "{i}. {name} ({ticker}) {vol} shares", "ja-JP": "{i}. {name}（{ticker}）{vol}株", "zh-CN": "{i}. {name}（{ticker}）{vol}股"}
_TITLES = {"en-US": "News and filings used (original titles):", "ja-JP": "参照したニュース・開示（原題）：", "zh-CN": "参考的新闻与公告（原标题）："}


def price_sentence(result: dict, locale: str) -> str | None:
    """stock_tool 결과의 마지막 시세 한 줄(언어별). answer_guard._price_sentence와 같은 값."""
    prices = (result or {}).get("prices") or []
    if not result or not result.get("found") or not prices:
        return None
    last = prices[-1]
    out = _PRICE[locale].format(name=result.get("company_name"), date=last["price_date"], close=dbt.won(last["close_price"], locale))
    extra = []
    if last.get("change_pct") is not None:
        extra.append(_CHANGE[locale].format(pct=dbt.pct(last["change_pct"], locale)))
    if last.get("volume") is not None:
        extra.append(_VOLUME[locale].format(vol=f"{last['volume']:,}"))
    if extra:
        out += " " + _JOIN[locale].join(extra) + _END[locale]
    return out


def localize(answer: str, records: list[dict], locale: str, now: datetime | None = None) -> str | None:
    """단순 시세 답(called_via=simple_quote, 장중 안내·레버리지 안내 없음)만 언어별 템플릿으로."""
    from app.answer_guard import quote_answer

    if locale not in LOCALES or len(records) != 1:
        return None
    r = records[0].get("result")
    if records[0].get("tool_name") != "stock_tool" or not isinstance(r, dict) or r.get("called_via") != "simple_quote":
        return None
    if r.get("intraday_excluded"):
        return None
    base = quote_freshness.base_of(r)
    now = now or datetime.now(quote_freshness_kst())
    korean = quote_answer(r)
    st = r.get("price_status")  # 늦은 이유(app.agent._quote_freshness_line이 남김)
    if base is None or korean is None or answer != f"{korean}\n\n{quote_freshness.line(base, now, price_status=st)}":
        return None
    sentence = price_sentence(r, locale)
    return f"{sentence}\n\n{quote_freshness.line(base, now, locale, st)}" if sentence else None


def quote_freshness_kst():
    from app.briefing import KST

    return KST


def fallback_summary(records: list[dict], locale: str, now: datetime | None = None) -> str:
    """번역 검증 실패 시 그 언어로 보여 줄 답: 고정 안내 + 도구 결과 데이터 요약 + 면책. 한국어 문장을 내보내지 않는다."""
    now = now or datetime.now(quote_freshness_kst())
    lines: list[str] = []
    titles: list[str] = []
    for rec in records or []:
        r = rec.get("result")
        if not isinstance(r, dict) or not r.get("found"):
            continue
        name = rec.get("tool_name")
        if name == "stock_tool":
            s = price_sentence(r, locale)
            base = quote_freshness.base_of(r)
            if s:
                lines.append(f"- {s}" + (f" {quote_freshness.line(base, now, locale, r.get('price_status'))}" if base else ""))
        elif name == "discovery_tool" and r.get("items") and r.get("category") in _RANK_HEAD:
            lines.append(_RANK_HEAD[r["category"]][locale].format(date=r.get("price_date")))
            lines += [_RANK_ITEM[locale].format(i=i, name=it.get("company_name"), ticker=it.get("ticker"), vol=f"{it.get('volume') or 0:,}")
                      for i, it in enumerate(r["items"][:10], 1)]
            if r.get("price_date"):
                lines.append(quote_freshness.line(date.fromisoformat(r["price_date"]), now, locale))
        for key in ("news", "disclosures", "results"):
            for it in (r.get(key) or [])[:5]:
                t = it.get("title")
                d = str(it.get("published_at") or it.get("disclosed_at") or it.get("rcept_dt") or "")[:10]
                if t:
                    titles.append(f"- {t}" + (f" ({d})" if d else ""))
    parts = [text("translation_fallback_summary" if (lines or titles) else "translation_fallback_empty", locale)]
    if lines:
        parts.append("\n".join(lines))
    if titles:
        parts.append(_TITLES[locale] + "\n" + "\n".join(dict.fromkeys(titles)))
    parts.append(disclaimer(locale))
    return "\n\n".join(parts)
