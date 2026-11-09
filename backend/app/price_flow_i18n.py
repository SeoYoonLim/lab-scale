"""최근 N거래일 주가 흐름 답(app/price_flow)의 언어별 템플릿(2026-11-06). 값은 기록(stock_tool, called_via="price_flow")의 계산 결과 그대로다.

app/outlook_i18n과 같은 조건에서만 쓴다: 기록으로 다시 만든 한국어 답이 저장된 답과 글자까지 같고, 템플릿 답에 한국어 답에 없는
숫자가 없을 때. 데이터 경고·신선도 문장은 outlook_i18n과 같은 함수로 바꾸고, 한국어 경고와 개수가 다르면(모르는 경고) 쓰지 않는다.
못 쓰면 None을 돌려주고 호출측(app/answer_i18n)은 보호 번역으로 간다.
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from app import outlook as o, outlook_i18n as oi, price_flow as pf
from app.briefing import KST

_WORD = {
    o.REGIME_REBOUND: {"en-US": "short-term rebound attempt", "ja-JP": "短期反発の試み", "zh-CN": "短期反弹尝试"},
    o.REGIME_UP: {"en-US": "uptrend", "ja-JP": "上昇トレンド", "zh-CN": "上涨趋势"},
    o.REGIME_PULLBACK: {"en-US": "uptrend (in a pullback)", "ja-JP": "上昇トレンド（調整中）", "zh-CN": "上涨趋势（回调中）"},
    o.REGIME_DOWN: {"en-US": "downtrend", "ja-JP": "下落トレンド", "zh-CN": "下跌趋势"},
    o.REGIME_FLAT: {"en-US": "sideways (no clear direction)", "ja-JP": "横ばい（方向感なし）", "zh-CN": "横盘（方向不明）"},
    o.REGIME_HOLD: {"en-US": "trend unconfirmed", "ja-JP": "トレンド未確定", "zh-CN": "趋势未确定"},
}
_K = {"d20": f"{o.REGIME_DROP20_PCT:.0f}", "d60": f"{o.REGIME_DROP60_PCT:.0f}", "lo": f"{o.REGIME_REBOUND_FROM_LOW_PCT:.0f}",
      "t20": f"{o.REGIME_TREND20_PCT:.0f}", "t60": f"{o.REGIME_TREND60_PCT:.0f}", "dip": f"{o.REGIME_DIP5_PCT:.0f}"}
_WHY = {
    o.REGIME_REBOUND: {
        "en-US": "After a large decline (20-trading-day return {d20}% or lower, or 60-trading-day {d60}% or lower), there is a rebound sign "
                 "(5-trading-day return > 0, 5-day line > 20-day line, or at least +{lo}% above the lowest close of the last 20 trading days), "
                 "but the close is below the 20-day or 60-day line, so a trend reversal is not confirmed.",
        "ja-JP": "20取引日騰落率{d20}%以下または60取引日{d60}%以下の大きな下落の後に反発のサイン（5取引日騰落率 > 0、5日線 > 20日線、"
                 "直近20取引日の最安終値比 +{lo}%以上のいずれか）がありますが、終値が20日線または60日線を下回っているため、トレンド転換は確認されていません。",
        "zh-CN": "在20个交易日收益率{d20}%以下或60个交易日{d60}%以下的大幅下跌之后出现反弹信号（5个交易日收益率 > 0、5日线 > 20日线、"
                 "较最近20个交易日最低收盘价上涨+{lo}%以上，满足其一），但收盘价低于20日线或60日线，因此尚未确认趋势反转。"},
    o.REGIME_DOWN: {
        "en-US": "The close < 20-day line < 60-day line, and the 20-trading-day return is -{t20}% or lower or the 60-trading-day return "
                 "is -{t60}% or lower.",
        "ja-JP": "終値 < 20日線 < 60日線で、20取引日騰落率が-{t20}%以下または60取引日が-{t60}%以下です。",
        "zh-CN": "收盘价 < 20日线 < 60日线，且20个交易日收益率在-{t20}%以下或60个交易日在-{t60}%以下。"},
    o.REGIME_PULLBACK: {
        "en-US": "Within an uptrend (60-trading-day return +{t60}% or higher, close > 60-day line, 20-day line > 60-day line), the close "
                 "is below the 20-day line or the 5-trading-day return is {dip}% or lower.",
        "ja-JP": "60取引日騰落率+{t60}%以上、終値 > 60日線、20日線 > 60日線の上昇トレンドの中で、終値が20日線を下回るか5取引日騰落率が{dip}%以下です。",
        "zh-CN": "在上涨趋势中（60个交易日收益率+{t60}%以上、收盘价 > 60日线、20日线 > 60日线），收盘价低于20日线或5个交易日收益率在{dip}%以下。"},
    o.REGIME_UP: {
        "en-US": "The close > 20-day line > 60-day line, and the 20-trading-day return is +{t20}% or higher or the 60-trading-day return "
                 "is +{t60}% or higher.",
        "ja-JP": "終値 > 20日線 > 60日線で、20取引日騰落率が+{t20}%以上または60取引日が+{t60}%以上です。",
        "zh-CN": "收盘价 > 20日线 > 60日线，且20个交易日收益率在+{t20}%以上或60个交易日在+{t60}%以上。"},
    o.REGIME_FLAT: {
        "en-US": "None of the uptrend, downtrend or rebound-attempt conditions is met, so the direction is unclear.",
        "ja-JP": "上昇トレンド・下落トレンド・反発の試みのいずれの条件にも当てはまらず、方向感がはっきりしません。",
        "zh-CN": "不符合上涨趋势、下跌趋势或反弹尝试的任何条件，方向不明确。"},
}
_HOLD = {
    "data": {"en-US": "no judgment is made because of the data warning.", "ja-JP": "データ警告のため判断しません。",
             "zh-CN": "因数据警告不作判断。"},
    "rows": {"en-US": "not enough prices to compute the 60-day line and the 60-trading-day return, so no judgment is made.",
             "ja-JP": "60日線・60取引日騰落率を計算する株価データが不足しているため判断しません。",
             "zh-CN": "股价数据不足以计算60日线和60个交易日收益率，因此不作判断。"},
    "signals": {"en-US": "not enough indicator groups to judge, so no judgment is made.",
                "ja-JP": "判断に使う指標グループが不足しているため判断しません。", "zh-CN": "可用于判断的指标组不足，因此不作判断。"},
}
T = {
    "title": {"en-US": "{name}({ticker}) · price trend over the last {n} trading days", "ja-JP": "{name}({ticker}) · 直近{n}取引日の株価推移",
              "zh-CN": "{name}({ticker}) · 最近{n}个交易日股价走势"},
    "base": {"en-US": "- Data as of: {base} close ({fresh}, expected trading day {exp}) · answer generated {at} KST",
             "ja-JP": "- データ基準日: {base} 終値（{fresh}、想定取引日 {exp}）· 回答作成 {at} KST",
             "zh-CN": "- 数据基准日：{base} 收盘价（{fresh}，预期交易日 {exp}）· 回答生成于 {at} KST"},
    "warn": {"en-US": "- Data warning: {w}", "ja-JP": "- データ警告: {w}", "zh-CN": "- 数据警告：{w}"},
    "no_lag": {"en-US": "- Latest-data delay: none", "ja-JP": "- 最新データの遅延: なし", "zh-CN": "- 最新数据延迟：无"},
    "ret_head": {"en-US": "## Returns (base-date close {won})", "ja-JP": "## 騰落率（基準日終値 {won}）", "zh-CN": "## 收益率（基准日收盘价 {won}）"},
    "ret": {"en-US": "- {n} trading days: {pct} (vs. {d} close {won})", "ja-JP": "- {n}取引日: {pct}（{d} 終値 {won} 比）",
            "zh-CN": "- {n}个交易日：{pct}（相对 {d} 收盘价 {won}）"},
    "ret1": {"en-US": "- 1 trading day: {pct} (vs. {d} close {won})", "ja-JP": "- 1取引日: {pct}（{d} 終値 {won} 比）",
             "zh-CN": "- 1个交易日：{pct}（相对 {d} 收盘价 {won}）"},
    "ret_none": {"en-US": "- {n} trading days: not enough price data ({rows} trading days available)",
                 "ja-JP": "- {n}取引日: 計算に必要な株価データ不足（{rows}取引日分）", "zh-CN": "- {n}个交易日：股价数据不足（仅{rows}个交易日）"},
    "ma_head": {"en-US": "## Moving averages", "ja-JP": "## 移動平均", "zh-CN": "## 移动平均线"},
    "ma": {"en-US": "- {n}-day moving average: {won}{gap}", "ja-JP": "- {n}日移動平均: {won}{gap}", "zh-CN": "- {n}日移动平均：{won}{gap}"},
    "ma_none": {"en-US": "- {n}-day moving average: not enough price data", "ja-JP": "- {n}日移動平均: 株価データ不足",
                "zh-CN": "- {n}日移动平均：股价数据不足"},
    "gap_up": {"en-US": " (close is {p}% above)", "ja-JP": "（終値が{p}%上）", "zh-CN": "（收盘价高出{p}%）"},
    "gap_down": {"en-US": " (close is {p}% below)", "ja-JP": "（終値が{p}%下）", "zh-CN": "（收盘价低{p}%）"},
    "gap_eq": {"en-US": " (equal to the close)", "ja-JP": "（終値と同じ）", "zh-CN": "（与收盘价相同）"},
    "rel_up": {"en-US": "- The {s}-day line is above the {l}-day line.", "ja-JP": "- {s}日線は{l}日線より上にあります。",
               "zh-CN": "- {s}日线位于{l}日线上方。"},
    "rel_down": {"en-US": "- The {s}-day line is below the {l}-day line.", "ja-JP": "- {s}日線は{l}日線より下にあります。",
                 "zh-CN": "- {s}日线位于{l}日线下方。"},
    "rel_eq": {"en-US": "- The {s}-day line is at the same level as the {l}-day line.", "ja-JP": "- {s}日線と{l}日線は同じ水準です。",
               "zh-CN": "- {s}日线与{l}日线持平。"},
    "vol_head": {"en-US": "## Volume", "ja-JP": "## 出来高", "zh-CN": "## 成交量"},
    "vol_none": {"en-US": "- No volume data for the base date.", "ja-JP": "- 基準日の出来高データがありません。", "zh-CN": "- 无基准日成交量数据。"},
    "vol": {"en-US": "- Volume on the base date ({d}): {v} shares", "ja-JP": "- 基準日（{d}）の出来高: {v}株", "zh-CN": "- 基准日（{d}）成交量：{v}股"},
    "avg_none": {"en-US": "- Average of the previous {n} trading days: not enough price data", "ja-JP": "- 直前{n}取引日の平均: 株価データ不足",
                 "zh-CN": "- 前{n}个交易日平均：股价数据不足"},
    "avg": {"en-US": "- Average of the previous {n} trading days ({f}~{t}, excluding the base date): {v} shares",
            "ja-JP": "- 直前{n}取引日の平均（{f}~{t}、基準日を除く）: {v}株", "zh-CN": "- 前{n}个交易日平均（{f}~{t}，不含基准日）：{v}股"},
    "ratio": {"en-US": "- Base-date volume is {r}x the average.", "ja-JP": "- 基準日の出来高は平均の{r}倍です。", "zh-CN": "- 基准日成交量为平均值的{r}倍。"},
    "rng_head": {"en-US": "## Range over the last {n} trading days", "ja-JP": "## 直近{n}取引日のレンジ", "zh-CN": "## 最近{n}个交易日区间"},
    "rng_short": {"en-US": "- Only {rows} trading days of prices are available, so the range covers {n} trading days.",
                  "ja-JP": "- 株価データが{rows}取引日分しかないため、{n}取引日のレンジで計算しました。",
                  "zh-CN": "- 股价数据仅有{rows}个交易日，因此按{n}个交易日区间计算。"},
    "rng_ret": {"en-US": "- Period return: {pct} (vs. {d} close)", "ja-JP": "- 期間騰落率: {pct}（{d} 終値比）", "zh-CN": "- 区间收益率：{pct}（相对 {d} 收盘价）"},
    "rng_hl": {"en-US": "- Highest close {hi} ({hd}), lowest close {lo} ({ld})", "ja-JP": "- 最高終値 {hi}（{hd}）、最安終値 {lo}（{ld}）",
               "zh-CN": "- 最高收盘价 {hi}（{hd}），最低收盘价 {lo}（{ld}）"},
    "reg_head": {"en-US": "## Market phase: {w}", "ja-JP": "## 局面判断: {w}", "zh-CN": "## 阶段判断：{w}"},
    "reg_hold": {"en-US": "- Insufficient basis: {x}", "ja-JP": "- 根拠不足: {x}", "zh-CN": "- 依据不足：{x}"},
    "reg_rule": {"en-US": "- Rule-based result: {x}", "ja-JP": "- コード規則による判定: {x}", "zh-CN": "- 代码规则判定：{x}"},
    "reg_why": {"en-US": "- Basis: {x}", "ja-JP": "- 判定根拠: {x}", "zh-CN": "- 判定依据：{x}"},
    "reg_vals": {"en-US": "- Values used: close {c}, 20-day line {m20}, 60-day line {m60}, 5 trading days {r5}, 20 trading days {r20}, "
                          "60 trading days {r60}, vs. the lowest close of the last 20 trading days {lo}",
                 "ja-JP": "- 判定に使った値: 終値 {c}、20日線 {m20}、60日線 {m60}、5取引日 {r5}、20取引日 {r20}、60取引日 {r60}、"
                          "直近20取引日の最安終値比 {lo}",
                 "zh-CN": "- 判定所用数值：收盘价 {c}，20日线 {m20}，60日线 {m60}，5个交易日 {r5}，20个交易日 {r20}，60个交易日 {r60}，"
                          "较最近20个交易日最低收盘价 {lo}"},
    "note": {"en-US": "- This summarizes prices up to now; it does not predict future prices or recommend trades.",
             "ja-JP": "- これまでの株価を整理したもので、将来の価格を予測したり売買を勧めたりするものではありません。",
             "zh-CN": "- 以上是对目前为止股价的整理，并非预测未来价格或建议买卖。"},
}


def _t(key: str, L: str, **kw) -> str:
    return T[key][L].format(**kw)


def _gap(pct: float | None, L: str) -> str:
    if pct is None:
        return ""
    if abs(pct) < 0.005:
        return _t("gap_eq", L)
    return _t("gap_up" if pct > 0 else "gap_down", L, p=f"{abs(pct):.2f}")


def answer(company, flow: dict, res: dict, now: datetime, L: str) -> str | None:
    """app.price_flow.answer와 같은 줄 구성. 데이터 경고를 바꾸지 못하면 None."""
    ctx = oi._Ctx(company, o.PERIODS[o.DEFAULT_PERIOD], res, L)
    warns = ctx.warnings()
    if warns is None:
        return None
    data, ind = res["data"], res["indicators"]
    regime = o.classify_regime(ind, res["label"])
    won = lambda v: oi._won(v, L)  # noqa: E731
    lines = [_t("title", L, name=company.name, ticker=company.ticker, n=flow["window"]["asked"]), ""]
    lines.append(_t("base", L, base=flow["base_date"], fresh=ctx.freshness(), exp=data["expected_date"],
                    at=f"{now.astimezone(KST):%Y-%m-%d %H:%M}"))
    lines += [_t("warn", L, w=w) for w in warns]
    if not warns:
        lines.append(_t("no_lag", L))

    lines += ["", _t("ret_head", L, won=won(flow["close"]))]
    for n, r in flow["returns"].items():
        if r is None:
            lines.append(_t("ret_none", L, n=n, rows=flow["n_rows"]))
        else:
            lines.append(_t("ret1" if n == 1 else "ret", L, n=n, pct=o._signed(r["pct"]), d=r["from_date"], won=won(r["from_close"])))

    lines += ["", _t("ma_head", L)]
    for n, m in flow["mas"].items():
        lines.append(_t("ma_none", L, n=n) if m is None else _t("ma", L, n=n, won=won(m["value"]), gap=_gap(m["gap_pct"], L)))
    known = [(n, m["value"]) for n, m in flow["mas"].items() if m is not None]
    if len(known) >= 2:
        (s, sv), (lg, lv) = known[0], known[-1]
        lines.append(_t("rel_up" if sv > lv else "rel_down" if sv < lv else "rel_eq", L, s=s, l=lg))

    v = flow["volume"]
    lines += ["", _t("vol_head", L)]
    if v.get("value") is None:
        lines.append(_t("vol_none", L))
    else:
        lines.append(_t("vol", L, d=flow["base_date"], v=f"{v['value']:,}"))
        if v.get("avg") is None:
            lines.append(_t("avg_none", L, n=v["days"]))
        else:
            lines.append(_t("avg", L, n=v["days"], f=v["from_date"], t=v["to_date"], v=f"{v['avg']:,}"))
            lines.append(_t("ratio", L, r=f"{v['ratio']:.2f}"))

    wd = flow["window"]
    lines += ["", _t("rng_head", L, n=wd["days"])]
    if wd["days"] < wd["asked"]:
        lines.append(_t("rng_short", L, rows=flow["n_rows"], n=wd["days"]))
    lines.append(_t("rng_ret", L, pct=o._signed(wd["pct"]), d=wd["from_date"]))
    lines.append(_t("rng_hl", L, hi=won(wd["high"]), hd=wd["high_date"], lo=won(wd["low"]), ld=wd["low_date"]))

    ri = ind.get("regime_inputs") or {}
    lines += ["", _t("reg_head", L, w=_WORD[regime][L])]
    if regime == o.REGIME_HOLD:
        lines.append(_t("reg_hold", L, x=_HOLD[pf.hold_reason(res)][L]))
    else:
        lines.append(_t("reg_rule", L, x=oi._REGIME[regime][L]))
        lines.append(_t("reg_why", L, x=_WHY[regime][L].format(**_K)))
        lines.append(_t("reg_vals", L, c=won(ind["close"]), m20=won(ri["ma20"]), m60=won(ri["ma60"]), r5=o._signed(ri["r5"]),
                        r20=o._signed(ri["r20"]), r60=o._signed(ri["r60"]), lo=o._signed(ri["from_low20_pct"])))
    lines += ["", _t("note", L)]
    return "\n".join(lines)


def localize(answer_text: str, records: list[dict], locale: str) -> str | None:
    """주가 흐름 경로의 한국어 답을 그 언어 템플릿으로. 조건을 못 맞추면 None."""
    rec = next((r for r in records or [] if r.get("tool_name") == "stock_tool"), None)
    result = (rec or {}).get("result") or {}
    if result.get("called_via") != "price_flow" or locale not in T["title"]:
        return None
    try:
        flow = result["flow"]
        res = {k: result[k] for k in ("indicators", "signals", "label", "data")}
        company = SimpleNamespace(name=result["company_name"], ticker=result["ticker"], asset_type=None)
        m = oi._AT_RE.search(answer_text)
        if m is None:
            return None
        now = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M").replace(tzinfo=KST)
        if pf.answer(company, flow, res, now) != answer_text:
            return None
        out = answer(company, flow, res, now, locale)
    except (KeyError, TypeError, ValueError, IndexError, AttributeError):
        return None
    if out is None or not oi._numbers(out) <= oi._numbers(answer_text) or out.count("\n") != answer_text.count("\n"):
        return None
    return out
