"""포트폴리오 진단 문장의 언어별 템플릿(2026-11-05, en-US·ja-JP·zh-CN). ko-KR은 app.diagnosis의 기존 문장을 그대로 쓴다.

app.diagnosis의 compute_flags·rule_based_text·holding_fact·fact_sentences·build_diagnosis(notes)와 **같은 조건, 같은 숫자**로
문장만 바꾼다(tests/test_diagnosis_i18n.py가 문장 수·숫자가 한국어판과 같은지 확인한다). 지표·플래그 계산은 하지 않는다.
LLM이 쓴 요약·제안은 보호 번역(app.i18n_translate)으로 옮기고, 검증에 실패하면 이 모듈의 규칙 기반 문장으로 대신한다.
"""

from app.diagnosis import (
    BIG_LOSS_PCT,
    CASH_HIGH_PCT,
    CASH_LOW_PCT,
    CONCENTRATION_TOP1_PCT,
    MARKET_SKEW_PCT,
    MAX_HOLDING_FACTS,
    MIN_HOLDING_COUNT,
    RETURN_WINDOW_DAYS,
)
from app.etf import leverage_kind
from app.i18n import disclaimer
from app.i18n_translate import STATUS_TRANSLATED, translate

_LEVERAGE = {
    "레버리지": {"en-US": "leveraged", "ja-JP": "レバレッジ", "zh-CN": "杠杆"},
    "인버스": {"en-US": "inverse", "ja-JP": "インバース", "zh-CN": "反向"},
    "인버스 레버리지": {"en-US": "leveraged inverse", "ja-JP": "インバース・レバレッジ", "zh-CN": "反向杠杆"},
}

T = {
    "en-US": {
        "CONCENTRATION_HIGH": "The weight of the top 1 holding is {v}%, at or above the threshold ({t:g}%).",
        "FEW_HOLDINGS": "You hold {v} stocks, fewer than the threshold ({t} stocks).",
        "CASH_HIGH": "Cash weight is {v}%, at or above the threshold ({t:g}%).",
        "CASH_LOW": "Cash weight is {v}%, below the threshold ({t:g}%).",
        "BIG_LOSS": "{name}'s profit/loss rate is {v}%, at or below the threshold ({t:g}%).",
        "MARKET_SKEW": "{v}% of the stock valuation is concentrated in {market} (threshold {t:g}%).",
        "LEV_W": "{name} is a {kind} ETF and makes up {w}% of total assets. It tracks daily returns, so holding it for a long time can make its return differ from the underlying index.",
        "LEV": "{name} is a {kind} ETF. It tracks daily returns, so holding it for a long time can make its return differ from the underlying index.",
        "summary_head": "Of total assets of {total}, cash is {cash}",
        "summary_cash_pct": " ({pct}% weight)",
        "summary_count": ", and you hold {n} stocks.",
        "summary_pl": " Total unrealized profit/loss is {pl} ({pct}%).",
        "summary_flags": " The rule check found {n} item(s).",
        "summary_no_flags": " The rule check found nothing in particular.",
        "s_top1": "The weight of the top 1 holding is {v}%, so the portfolio is not heavily concentrated in one stock.",
        "s_etf": "You hold {n} holdings, including {e} ETF(s). An ETF spreads investment across many assets within one product, so diversification was not judged by the number of holdings alone.",
        "s_spread": "Your holdings are spread across {n} stocks.",
        "s_best": "{name} has the highest profit/loss rate at {v}%.",
        "s_cash": "Cash weight is {v}%, within the rule's range.",
        "s_none": "It is hard to identify a clear strength based on the rules.",
        "no_risk": "No risk items were found based on the rules.",
        "sug": {
            "CONCENTRATION_HIGH": "You could consider ways to diversify by lowering the weight of a single stock.",
            "FEW_HOLDINGS": "You could consider also holding stocks from different sectors or markets.",
            "CASH_HIGH": "You could review why your cash weight is high and what your investment plan is.",
            "CASH_LOW": "You could consider keeping some cash in reserve for an unexpected decline.",
            "BIG_LOSS": "For stocks with large losses, you could check whether your original reason for buying still holds.",
            "MARKET_SKEW": "You could also consider looking at stocks in other markets.",
            "LEVERAGED_INVERSE_ETF": "You could separately review the holding period and weight of leveraged and inverse ETFs.",
        },
        "sug_default": "You could keep your current composition while regularly checking news and filings for each stock.",
        "fact_head": "{name} ",
        "fact_w": "makes up {w}% of total assets, and ",
        "fact_tail": "its price change over the last {d} trading days is {r:+}%.",
        "fact_tail_only": "has a price change of {r:+}% over the last {d} trading days.",
        "n_unpriced": "The current price of {names} could not be retrieved, so it was excluded from valuation, weight, and profit/loss calculations.",
        "n_db_price": "{names} was calculated with the latest closing price stored in the DB instead of a real-time quote.",
        "n_no_return": "There is not enough stored price data for {names} to calculate the return over the last {d} trading days.",
        "n_llm_fail": "The AI explanation could not be generated, so rule-based sentences were used instead.",
        "n_translate_fail": "The AI explanation could not be translated reliably, so rule-based sentences were used instead.",
    },
    "ja-JP": {
        "CONCENTRATION_HIGH": "上位1銘柄の比率が{v}%で、基準({t:g}%)以上です。",
        "FEW_HOLDINGS": "保有銘柄が{v}銘柄で、基準({t}銘柄)未満です。",
        "CASH_HIGH": "現金比率が{v}%で、基準({t:g}%)以上です。",
        "CASH_LOW": "現金比率が{v}%で、基準({t:g}%)未満です。",
        "BIG_LOSS": "{name}の損益率が{v}%で、基準({t:g}%)以下です。",
        "MARKET_SKEW": "株式評価額の{v}%が{market}に集中しています(基準{t:g}%)。",
        "LEV_W": "{name}は{kind}ETFで、総資産の{w}%です。日々の値動きを基準に運用されるため、長期保有すると基礎指数の収益率と異なる場合があります。",
        "LEV": "{name}は{kind}ETFです。日々の値動きを基準に運用されるため、長期保有すると基礎指数の収益率と異なる場合があります。",
        "summary_head": "総資産{total}のうち、現金は{cash}",
        "summary_cash_pct": "(比率{pct}%)",
        "summary_count": "で、保有銘柄は{n}銘柄です。",
        "summary_pl": "評価損益の合計は{pl}({pct}%)です。",
        "summary_flags": "ルール点検で{n}件の項目が確認されました。",
        "summary_no_flags": "ルール点検で特に確認された項目はありません。",
        "s_top1": "上位1銘柄の比率が{v}%で、特定の銘柄に大きく偏っていません。",
        "s_etf": "保有銘柄は{n}銘柄ですが、ETFが{e}銘柄含まれています。ETFはひとつの商品の中で複数の資産に分散投資する商品のため、銘柄数だけで分散の程度を判断しませんでした。",
        "s_spread": "{n}銘柄に分散して保有しています。",
        "s_best": "{name}の損益率が{v}%で最も高くなっています。",
        "s_cash": "現金比率が{v}%で、ルールの基準範囲内にあります。",
        "s_none": "ルール基準では明確な強みを判断するのが難しい状態です。",
        "no_risk": "ルール基準で確認されたリスク項目はありません。",
        "sug": {
            "CONCENTRATION_HIGH": "単一銘柄の比率を下げて分散する方法を検討できます。",
            "FEW_HOLDINGS": "業種や市場が異なる銘柄もあわせて保有する方法を検討できます。",
            "CASH_HIGH": "現金比率が高い理由と投資計画を点検できます。",
            "CASH_LOW": "予期しない下落に備えた現金の余裕を検討できます。",
            "BIG_LOSS": "損失が大きい銘柄は、最初に購入した理由が今も有効か点検できます。",
            "MARKET_SKEW": "他の市場の銘柄もあわせて見る方法を検討できます。",
            "LEVERAGED_INVERSE_ETF": "レバレッジ・インバースETFは保有期間と比率を別途点検できます。",
        },
        "sug_default": "現在の構成を維持しながら、銘柄ごとのニュースや開示を定期的に確認できます。",
        "fact_head": "{name}は",
        "fact_w": "総資産の{w}%で、",
        "fact_tail": "直近{d}取引日の株価騰落率は{r:+}%です。",
        "fact_tail_only": "直近{d}取引日の株価騰落率が{r:+}%です。",
        "n_unpriced": "{names}の現在値を取得できなかったため、評価額・比率・損益の計算から除外しました。",
        "n_db_price": "{names}はリアルタイム相場の代わりにDBに保存された直近の終値で計算しました。",
        "n_no_return": "{names}は保存された株価が不足しているため、直近{d}取引日の収益率を計算できませんでした。",
        "n_llm_fail": "AIによる説明を生成できなかったため、ルールベースの文章で代替しました。",
        "n_translate_fail": "AIによる説明を正確に翻訳できなかったため、ルールベースの文章で代替しました。",
    },
    "zh-CN": {
        "CONCENTRATION_HIGH": "前1大持仓的权重为{v}%，达到或超过基准({t:g}%)。",
        "FEW_HOLDINGS": "持有股票{v}只，少于基准({t}只)。",
        "CASH_HIGH": "现金权重为{v}%，达到或超过基准({t:g}%)。",
        "CASH_LOW": "现金权重为{v}%，低于基准({t:g}%)。",
        "BIG_LOSS": "{name}的盈亏率为{v}%，达到或低于基准({t:g}%)。",
        "MARKET_SKEW": "股票市值的{v}%集中在{market}(基准{t:g}%)。",
        "LEV_W": "{name}是{kind}ETF，占总资产的{w}%。它以单日收益率为基准运作，长期持有时收益率可能与基础指数不同。",
        "LEV": "{name}是{kind}ETF。它以单日收益率为基准运作，长期持有时收益率可能与基础指数不同。",
        "summary_head": "总资产{total}中，现金为{cash}",
        "summary_cash_pct": "(权重{pct}%)",
        "summary_count": "，持有股票{n}只。",
        "summary_pl": "总浮动盈亏为{pl}({pct}%)。",
        "summary_flags": "规则检查发现{n}项。",
        "summary_no_flags": "规则检查未发现特别需要关注的项目。",
        "s_top1": "前1大持仓的权重为{v}%，没有过度集中于单只股票。",
        "s_etf": "持有股票{n}只，其中包含{e}只ETF。ETF在一个产品中分散投资于多种资产，因此没有仅凭持股数量判断分散程度。",
        "s_spread": "分散持有{n}只股票。",
        "s_best": "{name}的盈亏率为{v}%，为最高。",
        "s_cash": "现金权重为{v}%，处于规则基准范围内。",
        "s_none": "按规则基准难以判断明显的优势。",
        "no_risk": "按规则基准未发现风险项目。",
        "sug": {
            "CONCENTRATION_HIGH": "可以考虑降低单只股票权重以分散投资。",
            "FEW_HOLDINGS": "可以考虑同时持有不同行业或市场的股票。",
            "CASH_HIGH": "可以检查现金权重较高的原因以及投资计划。",
            "CASH_LOW": "可以考虑为意外下跌预留一定现金。",
            "BIG_LOSS": "对于亏损较大的股票，可以检查最初买入的理由是否仍然成立。",
            "MARKET_SKEW": "可以考虑同时关注其他市场的股票。",
            "LEVERAGED_INVERSE_ETF": "可以另行检查杠杆和反向ETF的持有期限与权重。",
        },
        "sug_default": "可以在保持当前组合的同时，定期查看各股票的新闻和公告。",
        "fact_head": "{name}",
        "fact_w": "占总资产的{w}%，",
        "fact_tail": "最近{d}个交易日股价涨跌幅为{r:+}%。",
        "fact_tail_only": "最近{d}个交易日股价涨跌幅为{r:+}%。",
        "n_unpriced": "未能获取{names}的现价，已将其从市值、权重和盈亏计算中排除。",
        "n_db_price": "{names}使用数据库中保存的最近收盘价而非实时行情计算。",
        "n_no_return": "{names}保存的股价数据不足，无法计算最近{d}个交易日的收益率。",
        "n_llm_fail": "未能生成 AI 说明，已改用基于规则的文字。",
        "n_translate_fail": "未能可靠地翻译 AI 说明，已改用基于规则的文字。",
    },
}
_SEP = {"en-US": ", ", "ja-JP": "、", "zh-CN": "、"}
_CURRENCY = {"en-US": ("KRW ", ""), "ja-JP": ("", "ウォン"), "zh-CN": ("", "韩元")}


def won(x: float, locale: str) -> str:
    """app.diagnosis._won과 같은 값(정수 반올림·쉼표), 통화 표기만 언어별."""
    prefix, suffix = _CURRENCY[locale]
    return f"{prefix}{x:,.0f}{suffix}"


def _join(names: list[str], locale: str) -> str:
    return _SEP[locale].join(names)


def flag_message(flag: dict, items: list[dict], locale: str) -> str:
    """compute_flags가 만든 플래그 하나의 문장을 그 언어로. 값·기준은 플래그에 저장된 그대로다."""
    t = T[locale]
    code = flag["code"]
    if code == "LEVERAGED_INVERSE_ETF":
        item = next((i for i in items if i["ticker"] == flag.get("ticker")), None)
        kind = _LEVERAGE[leverage_kind(flag["company_name"])][locale]
        w = item["weight_pct"] if item else None
        key = "LEV_W" if w is not None else "LEV"
        return t[key].format(name=flag["company_name"], kind=kind, w=w)
    return t[code].format(v=flag["value"], t=flag["threshold"], name=flag.get("company_name"), market=flag.get("market"))


def rule_based_text(items: list[dict], metrics: dict, flags: list[dict], locale: str) -> dict:
    """app.diagnosis.rule_based_text와 같은 조건·같은 숫자. flags의 message는 이미 그 언어여야 한다(risks로 쓴다)."""
    t = T[locale]
    m = metrics
    codes = {f["code"] for f in flags}
    summary = t["summary_head"].format(total=won(m["total_asset"], locale), cash=won(m["cash_balance"], locale))
    if m["cash_weight_pct"] is not None:
        summary += t["summary_cash_pct"].format(pct=m["cash_weight_pct"])
    summary += t["summary_count"].format(n=m["holding_count"])
    if m["total_profit_loss_pct"] is not None:
        summary += t["summary_pl"].format(pl=won(m["total_profit_loss"], locale), pct=m["total_profit_loss_pct"])
    summary += t["summary_flags"].format(n=len(flags)) if flags else t["summary_no_flags"]

    strengths = []
    if m["top1_weight_pct"] is not None and "CONCENTRATION_HIGH" not in codes:
        strengths.append(t["s_top1"].format(v=m["top1_weight_pct"]))
    etf_count = sum(1 for i in items if i.get("asset_type") == "etf")
    if etf_count and m["holding_count"] < MIN_HOLDING_COUNT:
        strengths.append(t["s_etf"].format(n=m["holding_count"], e=etf_count))
    elif "FEW_HOLDINGS" not in codes:
        strengths.append(t["s_spread"].format(n=m["holding_count"]))
    b = m["best_holding"]
    if b and b["profit_loss_pct"] is not None and b["profit_loss_pct"] > 0:
        strengths.append(t["s_best"].format(name=b["company_name"], v=b["profit_loss_pct"]))
    if not codes & {"CASH_HIGH", "CASH_LOW"} and m["cash_weight_pct"] is not None:
        strengths.append(t["s_cash"].format(v=m["cash_weight_pct"]))
    if not strengths:
        strengths.append(t["s_none"])

    risks = [f["message"] for f in flags] or [t["no_risk"]]
    suggestions = [t["sug"][c] for c in t["sug"] if c in codes] or [t["sug_default"]]
    return {"summary": summary, "strengths": strengths, "risks": risks, "suggestions": suggestions}


def holding_fact(item: dict, locale: str) -> str:
    t = T[locale]
    head = t["fact_head"].format(name=item["company_name"])
    if item["weight_pct"] is None:
        return head + t["fact_tail_only"].format(d=RETURN_WINDOW_DAYS, r=item["return_20d_pct"])
    return head + t["fact_w"].format(w=item["weight_pct"]) + t["fact_tail"].format(d=RETURN_WINDOW_DAYS, r=item["return_20d_pct"])


def fact_sentences(items: list[dict], metrics: dict, flags: list[dict], locale: str) -> dict:
    """app.diagnosis.fact_sentences와 같은 종목·같은 순서."""
    base = rule_based_text(items, metrics, flags, locale)
    moved = sorted((i for i in items if i["return_20d_pct"]), key=lambda i: abs(i["return_20d_pct"]), reverse=True)
    up = [holding_fact(i, locale) for i in moved if i["return_20d_pct"] > 0][:MAX_HOLDING_FACTS]
    down = [holding_fact(i, locale) for i in moved if i["return_20d_pct"] < 0][:MAX_HOLDING_FACTS]
    return {"strengths": base["strengths"] + up, "risks": base["risks"] + down}


def data_notes(items: list[dict], locale: str) -> list[str]:
    """build_diagnosis의 시세 관련 notes와 같은 조건."""
    t = T[locale]
    notes = []
    unpriced = [i["company_name"] for i in items if i["price_unavailable"]]
    if unpriced:
        notes.append(t["n_unpriced"].format(names=_join(unpriced, locale)))
    fallback_priced = [i["company_name"] for i in items if not i["price_unavailable"] and not i["is_realtime"]]
    if fallback_priced:
        notes.append(t["n_db_price"].format(names=_join(fallback_priced, locale)))
    no_return = [i["company_name"] for i in items if i["return_20d_pct"] is None]
    if no_return:
        notes.append(t["n_no_return"].format(names=_join(no_return, locale), d=RETURN_WINDOW_DAYS))
    return notes


def localize(result: dict, items: list[dict], metrics: dict, locale: str) -> dict:
    """build_diagnosis의 한국어 결과를 그 언어로. 지표·플래그 값·종목·source 판정은 그대로 두고 문장만 바꾼다.

    - 규칙 기반(source="rule_based"): 이 모듈의 규칙 기반 문장
    - LLM(source="llm"): 강점·위험은 이 모듈의 코드 문장, 요약·제안은 보호 번역. 번역 검증에 실패하면 요약·제안도 규칙 기반 문장으로
      바꾸고 source를 rule_based로 둔다(안내 문구를 notes에 넣는다)."""
    t = T[locale]
    flags = [{**f, "message": flag_message(f, items, locale)} for f in result["flags"]]
    notes = data_notes(items, locale)
    source, model = result["source"], result["model"]
    if source == "llm":
        names = [i["company_name"] for i in items]
        summary = translate(result["summary"], locale, names)
        suggestions = [translate(s, locale, names) for s in result["suggestions"]]
        if summary.status == STATUS_TRANSLATED and all(s.status == STATUS_TRANSLATED for s in suggestions):
            facts = fact_sentences(items, metrics, flags, locale)
            text = {"summary": _strip_note(summary.text, locale), "strengths": facts["strengths"], "risks": facts["risks"],
                    "suggestions": [_strip_note(s.text, locale) for s in suggestions]}
        else:
            text = rule_based_text(items, metrics, flags, locale)
            notes.append(t["n_translate_fail"])
            source, model = "rule_based", None
    else:
        text = rule_based_text(items, metrics, flags, locale)
        notes.append(t["n_llm_fail"])
    return {**result, "source": source, "model": model, "flags": flags, **text, "notes": notes, "disclaimer": disclaimer(locale)}


def _strip_note(translated: str, locale: str) -> str:
    """번역문 끝의 '번역 안내' 줄은 진단 문장마다 붙이지 않는다(진단은 notes가 따로 있다)."""
    from app.i18n import text as fixed

    note = fixed("translation_note", locale)
    return translated.removesuffix(f"\n\n{note}").strip()
