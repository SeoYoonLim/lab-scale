"""언어별 시세 문장과 번역 실패 폴백(2026-11-07 35차).

1. 단순 시세 답(app.agent._answer_simple_quote)은 번역하지 않고 같은 도구 결과로 언어별 템플릿을 바로 쓴다. 기록으로 다시 만든
   한국어 답이 저장된 답과 글자까지 같을 때만 쓴다(outlook_i18n·price_flow_i18n과 같은 조건). 아니면 None -> 보호 번역.
2. 보호 번역(app.i18n_translate)이 검증에 실패하면 예전에는 "안내 + 한국어 원문"이었다(33차 live: 일본어·영어 화면에 한국어 답).
   이제 그 언어의 고정 안내 + 도구 결과로 만든 데이터 요약(종가·등락률·거래량·기준일·지연, 거래량 순위, 근거 기사·공시 제목) + 면책이다.
   숫자·날짜·종목코드는 도구 결과 값 그대로이고 표기만 언어 규칙(daily_briefing_text.won/pct)을 따른다. 종목명·기사/공시 제목은
   기존 정책대로 원문이다. LLM이 쓴 해석 문장은 옮기지 못했다고 밝힌다(계산 실패가 아니다).
"""

from __future__ import annotations

import re
from datetime import date, datetime

from app import daily_briefing_text as dbt, quote_freshness
from app.display_dates import kst_day
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
# 2026-11-23: 제목은 번역하지 않은 한국어 원문이라는 것을 그 언어로 밝힌다(영어 화면에 한국어 제목만 보이던 문제)
_TITLES = {"en-US": "News and filings used — original Korean headlines (shown in the source language):",
           "ja-JP": "参照したニュース・開示 — 韓国語の原文見出し（原文の言語のまま表示）：",
           "zh-CN": "参考的新闻与公告 — 韩语原文标题（按原文语言显示）："}


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


# ---------- 2026-11-23: 번역 실패 요약에 정량 분석 결과(기간 등락·최고·최저·최근 5거래일·지수 비교)를 남긴다 ----------
# 영어 원인 질문("Why did Samsung Electronics fall this week?")의 번역 검증이 실패하면 예전 요약에는 기사 제목만 남고 이미 계산한
# 기간 등락률(-7.62%)·기간·지수 비교가 사라졌다(기간 원인 답의 stock_tool 기록에는 prices가 없어 price_sentence가 None). 값은 도구 기록에
# 이미 있는 것만 쓰고(새로 계산하거나 만들지 않는다) 표기만 언어 규칙을 따른다. 한국어 답과 같은 기록이라 수치가 같다.
_INDEX_NAMES = {"코스피": "KOSPI", "코스닥": "KOSDAQ"}
_PERIOD = {
    "en-US": "{name}: {start} close → {end} close{closes}, {pct} ({n} trading days).",
    "ja-JP": "{name}：{start}終値→{end}終値{closes}、{pct}（{n}営業日）。",
    "zh-CN": "{name}：{start}收盘价→{end}收盘价{closes}，{pct}（{n}个交易日）。",
}
_CLOSES = {"en-US": " ({a} → {b})", "ja-JP": "（{a}→{b}）", "zh-CN": "（{a}→{b}）"}
_RANGE = {
    "en-US": "Closing high in this period: {high} ({hd}); closing low: {low} ({ld}).",
    "ja-JP": "期間中の終値の最高は{high}（{hd}）、最低は{low}（{ld}）です。",
    "zh-CN": "期间收盘价最高{high}（{hd}），最低{low}（{ld}）。",
}
_FIVE_DAY = {
    "en-US": "{name}: close {close} on {base}, daily change {day}; last 5 trading days {r} (vs. {frm} close {frm_close}).",
    "ja-JP": "{name}の{base}終値は{close}、前日比{day}、直近5営業日では{r}（{frm}終値{frm_close}比）です。",
    "zh-CN": "{name}{base}收盘价为{close}，较前一日{day}；最近5个交易日{r}（较{frm}收盘价{frm_close}）。",
}
_INDEX = {
    "en-US": "{index} over the same dates: {pct}. Difference ({name} minus {index}): {diff}%p.",
    "ja-JP": "同じ期間の{index}指数：{pct}。差（{name}−{index}）：{diff}%p。",
    "zh-CN": "同期{index}指数：{pct}。差值（{name}−{index}）：{diff}个百分点。",
}
_RELATIVE = {
    "en-US": "Market index over the same {n} trading days: {mpct}; {name}: {spct} (difference {diff}%p).",
    "ja-JP": "同じ{n}営業日の市場指数：{mpct}、{name}：{spct}（差{diff}%p）。",
    "zh-CN": "同{n}个交易日市场指数：{mpct}，{name}：{spct}（差值{diff}个百分点）。",
}
_TAIL = {
    "en-US": "Prices for the last {n} trading day(s) of the requested period are not stored yet, so this is not the change for the whole period.",
    "ja-JP": "ご指定の期間の最後の{n}営業日分の株価はまだ保存されていないため、期間全体の騰落ではありません。",
    "zh-CN": "所指定期间最后{n}个交易日的股价尚未保存，因此并非整个期间的涨跌。",
}
_CAUSE_NOTE = {
    "en-US": "These figures show the price move only. The news and filings below are listed for reference and are not confirmed as the direct cause of the move.",
    "ja-JP": "上記は株価の動きのみを示します。下記のニュース・開示は参考として示すもので、値動きの直接の原因と確認されたものではありません。",
    "zh-CN": "以上数字仅表示股价变动。以下新闻与公告仅供参考，并未被确认是股价变动的直接原因。",
}
_NO_DIRECT = {
    "en-US": "No news or filings directly about {name} were found in this period, so the company-specific cause of the move cannot be identified.",
    "ja-JP": "この期間に{name}を直接扱ったニュース・開示が見つからなかったため、{name}固有の値動きの原因は特定できません。",
    "zh-CN": "该期间未找到直接涉及{name}的新闻或公告，因此无法确定{name}股价变动的个别原因。",
}
_TITLES_REF = {
    "en-US": "Sector and market news for reference — original Korean headlines (not the company's direct cause):",
    "ja-JP": "業種・市場の参考ニュース — 韓国語の原文見出し（この銘柄の直接の原因ではありません）：",
    "zh-CN": "行业与市场参考新闻 — 韩语原文标题（并非该股票的直接原因）：",
}
_CAUSE_VIA = ("cause_period", "cause_only", "cause_forecast", "forecast_cause", "forecast_cause_forecast")


def _index_name(name: str | None) -> str:
    return _INDEX_NAMES.get(name or "", name or "KOSPI")


def _pp(v) -> str:
    return f"{float(v):+.2f}"


def _quant_lines(r: dict, locale: str) -> list[str]:
    """stock_tool 기록의 정량 결과 줄(기간 등락·종가 최고·최저, 원인·전망 경로의 최근 5거래일). 없으면 []."""
    name = r.get("company_name")
    out = []
    win = r.get("window") if isinstance(r.get("window"), dict) else {}
    if r.get("period_change_pct") is not None and r.get("period_start_date") and r.get("period_end_date"):
        a, b = win.get("from_close"), win.get("end_close")
        closes = _CLOSES[locale].format(a=dbt.won(a, locale), b=dbt.won(b, locale)) if a is not None and b is not None else ""
        n = r.get("trading_days") or win.get("trading_days")
        line = _PERIOD[locale].format(name=name, start=r["period_start_date"], end=r["period_end_date"], closes=closes,
                                      pct=dbt.pct(r["period_change_pct"], locale), n=n if n is not None else "?")
        if n is None:
            line = re.sub(r"\s?[(（][^()（）]*\?[^()（）]*[)）]", "", line)
        out.append(line)
        prices = [p for p in (r.get("prices") or []) if p.get("close_price") is not None]
        if len(prices) >= 2:
            hi = max(prices, key=lambda p: p["close_price"])
            lo = min(prices, key=lambda p: p["close_price"])
            out.append(_RANGE[locale].format(high=dbt.won(hi["close_price"], locale), hd=hi["price_date"],
                                             low=dbt.won(lo["close_price"], locale), ld=lo["price_date"]))
        if win.get("missing_tail"):
            out.append(_TAIL[locale].format(n=len(win["missing_tail"])))
    ind = r.get("indicators") if isinstance(r.get("indicators"), dict) else {}
    ri = ind.get("regime_inputs") or {}
    frm = ri.get("r5_from")
    if ind.get("base_date") and ind.get("close") is not None and ri.get("r5") is not None and frm:
        out.append(_FIVE_DAY[locale].format(name=name, close=dbt.won(ind["close"], locale), base=ind["base_date"],
                                            day=dbt.pct(ind.get("day_change_pct"), locale) if ind.get("day_change_pct") is not None else "-",
                                            r=dbt.pct(ri["r5"], locale), frm=frm[0], frm_close=dbt.won(frm[1], locale)))
    return out


def _market_lines(r: dict, locale: str) -> list[str]:
    name = r.get("company_name") or ""
    if r.get("market_change_pct") is None:
        return []
    if r.get("index_name") and r.get("stock_change_pct") is not None and r.get("start_date"):
        diff = round(float(r["stock_change_pct"]) - float(r["market_change_pct"]), 2)
        return [_INDEX[locale].format(index=_index_name(r["index_name"]), pct=dbt.pct(r["market_change_pct"], locale),
                                      name=name, diff=_pp(diff))]
    if r.get("stock_change_pct") is not None and r.get("period_days"):
        diff = r.get("diff_pct_point")
        if diff is None:
            diff = round(float(r["stock_change_pct"]) - float(r["market_change_pct"]), 2)
        return [_RELATIVE[locale].format(n=r["period_days"], mpct=dbt.pct(r["market_change_pct"], locale), name=name,
                                         spct=dbt.pct(r["stock_change_pct"], locale), diff=_pp(diff))]
    return []


def fallback_summary(records: list[dict], locale: str, now: datetime | None = None) -> str:
    """번역 검증 실패 시 그 언어로 보여 줄 답: 고정 안내 + 도구 결과 데이터 요약 + 면책. 한국어 문장을 내보내지 않는다."""
    from app import evidence_scope

    now = now or datetime.now(quote_freshness_kst())
    lines: list[str] = []
    titles: list[str] = []
    refs: list[str] = []
    cause_names: list[str] = []
    direct_seen = False
    for rec in records or []:
        r = rec.get("result")
        if not isinstance(r, dict) or not r.get("found"):
            continue
        name = rec.get("tool_name")
        if r.get("called_via") in _CAUSE_VIA and r.get("company_name") and r["company_name"] not in cause_names:
            cause_names.append(r["company_name"])
        if name == "stock_tool":
            s = price_sentence(r, locale)
            base = quote_freshness.base_of(r)
            win = r.get("window") if isinstance(r.get("window"), dict) else {}
            if base is None and win.get("last_stored"):
                base = date.fromisoformat(win["last_stored"])
            quant = _quant_lines(r, locale)
            if s:
                lines.append(f"- {s}" + (f" {quote_freshness.line(base, now, locale, r.get('price_status'))}" if base else ""))
            lines += [f"- {q}" for q in quant]
            if quant and not s and base:
                lines.append(f"- {quote_freshness.line(base, now, locale, r.get('price_status'))}")
        elif name == "market_tool":
            lines += [f"- {m}" for m in _market_lines(r, locale)]
        elif name == "discovery_tool" and r.get("items") and r.get("category") in _RANK_HEAD:
            lines.append(_RANK_HEAD[r["category"]][locale].format(date=r.get("price_date")))
            lines += [_RANK_ITEM[locale].format(i=i, name=it.get("company_name"), ticker=it.get("ticker"), vol=f"{it.get('volume') or 0:,}")
                      for i, it in enumerate(r["items"][:10], 1)]
            if r.get("price_date"):
                lines.append(quote_freshness.line(date.fromisoformat(r["price_date"]), now, locale))
        for key in ("news", "disclosures", "results"):
            items = [it for it in (r.get(key) or []) if isinstance(it, dict)]
            # 2026-11-23: 기사 범위(app/evidence_scope)가 정해진 기록이면 업종·시장 기사는 따로, 관련성 미확인 기사는 넣지 않는다
            direct = [it for it in items if not evidence_scope.is_reference(it.get("scope"))]
            ctx = [it for it in items if it.get("scope") in (evidence_scope.SECTOR, evidence_scope.MARKET)]
            for group, target in ((direct[:5], titles), (ctx[:3], refs)):
                for it in group:
                    t = it.get("title")
                    # 38차: UTC 시각의 앞 10자가 아니라 한국 날짜(공시가 하루 이르게 나오던 문제)
                    d = kst_day(it.get("published_at") or it.get("disclosed_at") or it.get("rcept_dt") or it.get("date"))
                    if t:
                        target.append(f"- {t}" + (f" ({d})" if d else ""))
            direct_seen = direct_seen or bool(direct)
    parts = [text("translation_fallback_summary" if (lines or titles or refs) else "translation_fallback_empty", locale)]
    if lines:
        parts.append("\n".join(lines))
    if cause_names:
        parts.append(_CAUSE_NOTE[locale])
        if not direct_seen:
            parts.append(" ".join(_NO_DIRECT[locale].format(name=n) for n in cause_names))
    if titles:
        parts.append(_TITLES[locale] + "\n" + "\n".join(dict.fromkeys(titles)))
    if refs:
        parts.append(_TITLES_REF[locale] + "\n" + "\n".join(dict.fromkeys(refs)))
    parts.append(disclaimer(locale))
    return "\n\n".join(parts)
