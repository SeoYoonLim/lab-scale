"""전망 간결 답·후속 답의 언어별 코드 템플릿(2026-11-06). ko-KR이 아닐 때만 app.answer_i18n이 부른다.

한국어 답(app.outlook)과 같은 계산 결과(stock_tool 기록의 indicators·signals·label·data)로 문장만 그 언어로 쓴다.
계산·판정·근거 선택은 app.outlook의 함수를 그대로 부르고 이 모듈은 문구만 가진다(app.outlook은 바꾸지 않는다).

쓰는 조건(하나라도 어기면 None → app.answer_i18n이 보호 번역으로 넘어간다):
1. 기록으로 한국어 답을 다시 만들면 저장된 한국어 답과 글자까지 같다(가드가 고친 답·다른 경로의 답에는 쓰지 않는다).
2. 템플릿 답의 숫자는 모두 한국어 답에 있는 숫자다(새 숫자를 만들지 않는다).
3. 이 모듈이 다루는 종류다: 간결 전망(brief)과 후속 답 verdict·pick·why·oneline·compare·probability·buy·risk·data.
   상세 리서치(forecast)·실적(earnings)·공시 날짜(filing)·신호 의미(signal)는 뉴스·공시 제목과 실적 행이 섞여 보호 번역으로 둔다.
"""

import re
from datetime import datetime
from types import SimpleNamespace

from app import outlook as o
from app.briefing import KST

SUPPORTED_MODES = ("brief", "verdict", "pick", "why", "oneline", "compare", "probability", "buy", "risk", "data")

# ---------- 낱말 ----------

_DIR = {
    o.DIR_UP: {"en-US": "upward bias", "ja-JP": "上昇寄り", "zh-CN": "偏上涨"},
    o.DIR_DOWN: {"en-US": "downward bias", "ja-JP": "下落寄り", "zh-CN": "偏下跌"},
    o.DIR_NEUTRAL: {"en-US": "neutral", "ja-JP": "中立", "zh-CN": "中性"},
    o.DIR_HOLD: {"en-US": "judgment withheld", "ja-JP": "判断保留", "zh-CN": "暂不判断"},
}
# 영어 문장 속 방향(제목은 _DIR)
_DIR_SENT_EN = {o.DIR_UP: "the direction leans upward", o.DIR_DOWN: "the direction leans downward"}
_CONF = {
    o.CONF_LOW: {"en-US": "low", "ja-JP": "低い", "zh-CN": "低"},
    o.CONF_MID: {"en-US": "medium", "ja-JP": "中程度", "zh-CN": "中等"},
    o.CONF_HIGH: {"en-US": "high", "ja-JP": "高い", "zh-CN": "高"},
}
_LABEL = {
    o.LABEL_UP: {"en-US": "upward lead", "ja-JP": "上昇優位", "zh-CN": "上涨占优"},
    o.LABEL_DOWN: {"en-US": "downward lead", "ja-JP": "下落優位", "zh-CN": "下跌占优"},
    o.LABEL_NEUTRAL: {"en-US": "neutral", "ja-JP": "中立", "zh-CN": "中性"},
    o.LABEL_HOLD: {"en-US": "judgment withheld", "ja-JP": "判断保留", "zh-CN": "暂不判断"},
}
_REGIME = {
    o.REGIME_REBOUND: {"en-US": "a short-term rebound attempt, with no confirmed trend reversal",
                       "ja-JP": "短期的な反発の試み（トレンド転換は未確認）", "zh-CN": "短期反弹尝试，趋势反转尚未确认"},
    o.REGIME_UP: {"en-US": "a continuing uptrend", "ja-JP": "上昇トレンド継続", "zh-CN": "上涨趋势延续"},
    o.REGIME_PULLBACK: {"en-US": "a pullback within an uptrend", "ja-JP": "上昇トレンド中の調整", "zh-CN": "上涨趋势中的回调"},
    o.REGIME_DOWN: {"en-US": "a continuing downtrend", "ja-JP": "下落トレンド継続", "zh-CN": "下跌趋势延续"},
    o.REGIME_FLAT: {"en-US": "sideways, with no clear direction", "ja-JP": "横ばい（方向感なし）", "zh-CN": "横盘，方向不明"},
    o.REGIME_HOLD: {"en-US": "judgment withheld (insufficient data)", "ja-JP": "判断保留（データ不足）", "zh-CN": "暂不判断（数据不足）"},
}
_REGIME_SHORT = {
    o.REGIME_REBOUND: {"en-US": "a rebound attempt after a decline (reversal not confirmed)",
                       "ja-JP": "下落後の反発の試み（トレンド転換は未確認）", "zh-CN": "下跌后尝试反弹（趋势反转未确认）"},
    o.REGIME_UP: {"en-US": "a continuing uptrend", "ja-JP": "上昇トレンド継続", "zh-CN": "上涨趋势延续"},
    o.REGIME_PULLBACK: {"en-US": "a pullback within an uptrend", "ja-JP": "上昇トレンド中の調整", "zh-CN": "上涨趋势中的回调"},
    o.REGIME_DOWN: {"en-US": "a continuing downtrend", "ja-JP": "下落トレンド継続", "zh-CN": "下跌趋势延续"},
    o.REGIME_FLAT: {"en-US": "sideways", "ja-JP": "横ばい", "zh-CN": "横盘"},
    o.REGIME_HOLD: {"en-US": "judgment withheld (insufficient data)", "ja-JP": "判断保留（データ不足）", "zh-CN": "暂不判断（数据不足）"},
}
_GROUP = {
    "단기 흐름": {"en-US": "short-term momentum", "ja-JP": "短期の流れ", "zh-CN": "短期走势"},
    "중기 추세": {"en-US": "medium-term trend", "ja-JP": "中期トレンド", "zh-CN": "中期趋势"},
    "RSI": {"en-US": "RSI", "ja-JP": "RSI", "zh-CN": "RSI"},
    "MACD": {"en-US": "MACD", "ja-JP": "MACD", "zh-CN": "MACD"},
    "거래량": {"en-US": "volume", "ja-JP": "出来高", "zh-CN": "成交量"},
}
_PERIOD = {
    "1주일": {"en-US": "1 week", "ja-JP": "1週間", "zh-CN": "1周"},
    "1개월": {"en-US": "1 month", "ja-JP": "1か月", "zh-CN": "1个月"},
    "3개월": {"en-US": "3 months", "ja-JP": "3か月", "zh-CN": "3个月"},
}
_INDEX = {"코스피": "KOSPI", "코스닥": "KOSDAQ"}
_PRICE_STATUS = {
    o.PRICE_STATUS_NOT_COLLECTED: {"en-US": "not collected", "ja-JP": "未収集", "zh-CN": "未采集"},
    o.PRICE_STATUS_FAILED: {"en-US": "collection failed", "ja-JP": "収集失敗", "zh-CN": "采集失败"},
    o.PRICE_STATUS_HOLIDAY: {"en-US": "presumed market holiday", "ja-JP": "休場日と推定", "zh-CN": "推定为休市日"},
    o.PRICE_STATUS_STOCK_ONLY: {"en-US": "missing for this stock only", "ja-JP": "この銘柄のみ欠落", "zh-CN": "仅该股票缺失"},
}

# 문장 틀. {…}는 코드가 채운다(숫자·날짜·이름은 계산 결과 그대로).
T: dict[str, dict[str, str]] = {
    "title": {"en-US": "{name}({ticker}) · next {days} trading days", "ja-JP": "{name}({ticker}) · 今後{days}取引日",
              "zh-CN": "{name}({ticker}) · 未来{days}个交易日"},
    "dir_title": {"en-US": "{title} · Direction: {d}", "ja-JP": "{title} · 方向: {d}", "zh-CN": "{title} · 方向：{d}"},
    "dir_conf": {"en-US": " (confidence: {conf})", "ja-JP": "（信頼度: {conf}）", "zh-CN": "（可信度：{conf}）"},
    "tally": {"en-US": "{total} signal groups: {up} up, {down} down, {neutral} neutral",
              "ja-JP": "{total}つのシグナル群のうち上昇{up}・下落{down}・中立{neutral}",
              "zh-CN": "{total}个信号组中上涨{up}、下跌{down}、中性{neutral}"},
    "state": {"en-US": "Currently {regime} (closing price on base date {base})", "ja-JP": "現在は{regime}（基準日{base}の終値）",
              "zh-CN": "目前为{regime}（基准日{base}收盘价）"},
    "when": {"en-US": " (based on the {base} close)", "ja-JP": "（{base}終値基準）", "zh-CN": "（以{base}收盘价为准）"},
    "hold_lag": {"en-US": "the latest price is {lag} business days behind", "ja-JP": "最新の株価が{lag}営業日遅れている",
                 "zh-CN": "最新股价滞后{lag}个工作日"},
    "hold_missing": {"en-US": "prices are missing for {n} trading days in the calculation window",
                     "ja-JP": "計算期間の{n}取引日分の株価がない", "zh-CN": "计算区间内有{n}个交易日没有股价"},
    "hold_groups": {"en-US": "only {total} signal groups could be calculated, fewer than {min}",
                    "ja-JP": "計算できたシグナル群が{total}つで{min}つ未満", "zh-CN": "可计算的信号组只有{total}个，少于{min}个"},
    "pick_hold": {"en-US": "Even if asked to pick one, ", "ja-JP": "一つだけ選ぶとしても、", "zh-CN": "即使只能选一个，"},
    "pick": {"en-US": "If I had to pick one: ", "ja-JP": "一つ選ぶなら、", "zh-CN": "如果只选一个，"},
    "hold_sentence": {"en-US": "there is not enough data, so I will withhold a directional judgment for now ({reason}; {tally}).",
                      "ja-JP": "データが不足しているため、現時点では方向の判断を保留します（{reason}、{tally}）。",
                      "zh-CN": "由于数据不足，目前暂不判断方向（{reason}，{tally}）。"},
    "neutral_gap": {"en-US": "; {side} groups lead by {gap}, short of the {margin}-group threshold",
                    "ja-JP": "、{side}側のシグナル群が{gap}つ多いものの基準の{margin}つの差に届きません",
                    "zh-CN": "，{side}方向的信号组多{gap}个，但未达到{margin}个的差距标准"},
    "neutral_sentence": {"en-US": "right now neither the upside nor the downside clearly dominates ({tally}{more}). "
                                  "The trend is {regime}{when}.",
                         "ja-JP": "現時点では上昇と下落のどちらも明確に優勢ではありません（{tally}{more}）。トレンドは{regime}です{when}。",
                         "zh-CN": "目前上涨和下跌均无明显优势（{tally}{more}）。趋势为{regime}{when}。"},
    "side_up": {"en-US": "upside", "ja-JP": "上昇", "zh-CN": "上涨"},
    "side_down": {"en-US": "downside", "ja-JP": "下落", "zh-CN": "下跌"},
    "weak": {"en-US": "the signal is weak ({tally})", "ja-JP": "シグナルが弱く（{tally}）", "zh-CN": "信号较弱（{tally}）"},
    "share": {"en-US": "the group split is {tally}", "ja-JP": "シグナル群の内訳は{tally}で", "zh-CN": "信号组构成为{tally}"},
    "trend_same": {"en-US": "the trend ({regime}) points the same way", "ja-JP": "トレンド（{regime}）も同じ方向ですが",
                   "zh-CN": "趋势（{regime}）方向一致"},
    "trend_other": {"en-US": "the trend is still at the stage of {regime}", "ja-JP": "トレンドはまだ{regime}の段階で",
                    "zh-CN": "趋势仍处于{regime}阶段"},
    "why_conf_unproven": {"en-US": "historical backtests did not confirm the directional power of such signals, so ",
                          "ja-JP": "過去検証でこうしたシグナルの方向予測力が確認されなかったため、",
                          "zh-CN": "历史回测未能证实此类信号的方向预测力，因此"},
    "why_conf_none": {"en-US": "there are no backtest results, so ", "ja-JP": "過去検証の結果がないため、",
                      "zh-CN": "由于没有历史回测结果，"},
    "conf_word": {"en-US": "confidence is {conf}", "ja-JP": "信頼度は{conf}です", "zh-CN": "可信度为{conf}"},
    "dir_sentence": {"en-US": "{pick}{d}. However, {strength}, {trend}, and {why}{conf}{when}.",
                     "ja-JP": "{pick}{d}です。ただし、{strength}、{trend}、{why}{conf}{when}。",
                     "zh-CN": "{pick}{d}。不过，{strength}，{trend}，{why}{conf}{when}。"},
    "core_dir": {"en-US": "{pick}{d}.", "ja-JP": "{pick}{d}です。", "zh-CN": "{pick}{d}。"},
    "core_neutral": {"en-US": "Right now neither the upside nor the downside clearly dominates.",
                     "ja-JP": "現時点では上昇と下落のどちらも明確に優勢ではありません。", "zh-CN": "目前上涨和下跌均无明显优势。"},
    "core_hold": {"en-US": "There is not enough data, so I will withhold a directional judgment for now.",
                  "ja-JP": "データが不足しているため、現時点では方向の判断を保留します。", "zh-CN": "由于数据不足，目前暂不判断方向。"},
    "none_up": {"en-US": "No signal group was judged upward.", "ja-JP": "上昇側と判定されたシグナル群はありません。",
                "zh-CN": "没有被判定为上涨方向的信号组。"},
    "none_down": {"en-US": "No signal group was judged downward.", "ja-JP": "下落側と判定されたシグナル群はありません。",
                  "zh-CN": "没有被判定为下跌方向的信号组。"},
    "opposite": {"en-US": " (opposite signal in the same group: {x})", "ja-JP": "（同じシグナル群の反対シグナル: {x}）",
                 "zh-CN": "（同组反向信号：{x}）"},
    "mixed": {"en-US": "[{g}] {ups} (up) but {downs} (down), so the group is neutral",
              "ja-JP": "[{g}] {ups}（上昇）ですが{downs}（下落）のため、シグナル群は中立です",
              "zh-CN": "[{g}] {ups}（上涨）但{downs}（下跌），因此该信号组为中性"},
    "mixed_label": {"en-US": "Mixed group: {x}", "ja-JP": "相反するシグナル群: {x}", "zh-CN": "方向不一的信号组：{x}"},
    "context_label": {"en-US": "Trend reference (outside the signal table): {x}", "ja-JP": "トレンド参考（シグナル判定表の外）: {x}",
                      "zh-CN": "趋势参考（不计入信号判定表）：{x}"},
    "ctx_return": {"en-US": "{n}-trading-day return {pct} (vs. the {d} close of {won})", "ja-JP": "{n}取引日の収益率{pct}（{d}終値{won}比）",
                   "zh-CN": "{n}个交易日收益率{pct}（相对{d}收盘价{won}）"},
    "ctx_ma60_up": {"en-US": "close above the 60-day MA ({won})", "ja-JP": "終値が60日線（{won}）の上", "zh-CN": "收盘价位于60日均线（{won}）上方"},
    "ctx_ma60_down": {"en-US": "close below the 60-day MA ({won})", "ja-JP": "終値が60日線（{won}）の下", "zh-CN": "收盘价位于60日均线（{won}）下方"},
    "ctx_day": {"en-US": "{pct} on the base date ({base})", "ja-JP": "基準日（{base}）当日{pct}", "zh-CN": "基准日（{base}）单日{pct}"},
    # 신호 한 개
    "s_return": {"en-US": "{n}-trading-day return {pct} (vs. the {d} close)", "ja-JP": "直近{n}取引日の収益率{pct}（{d}終値比）",
                 "zh-CN": "近{n}个交易日收益率{pct}（相对{d}收盘价）"},
    "s_close_ma_up": {"en-US": "close {close} above the {n}-day MA ({ma})", "ja-JP": "終値{close}が{n}日線（{ma}）の上",
                      "zh-CN": "收盘价{close}位于{n}日均线（{ma}）上方"},
    "s_close_ma_down": {"en-US": "close {close} below the {n}-day MA ({ma})", "ja-JP": "終値{close}が{n}日線（{ma}）の下",
                        "zh-CN": "收盘价{close}位于{n}日均线（{ma}）下方"},
    "s_align_up": {"en-US": "{s}-day MA {v1} above the {l}-day MA {v2}{cross}", "ja-JP": "{s}日線{v1}が{l}日線{v2}の上{cross}",
                   "zh-CN": "{s}日均线{v1}位于{l}日均线{v2}上方{cross}"},
    "s_align_down": {"en-US": "{s}-day MA {v1} below the {l}-day MA {v2}{cross}", "ja-JP": "{s}日線{v1}が{l}日線{v2}の下{cross}",
                     "zh-CN": "{s}日均线{v1}位于{l}日均线{v2}下方{cross}"},
    "golden": {"en-US": "golden cross on {d}", "ja-JP": "{d}ゴールデンクロス", "zh-CN": "{d}金叉"},
    "dead": {"en-US": "dead cross on {d}", "ja-JP": "{d}デッドクロス", "zh-CN": "{d}死叉"},
    "no_cross": {"en-US": "no crossover in the last {n} trading days", "ja-JP": "直近{n}取引日にクロスなし",
                 "zh-CN": "近{n}个交易日内无交叉"},
    "cross_sep": {"en-US": ", ", "ja-JP": "、", "zh-CN": "，"},
    "s_relative": {"en-US": "{pp}%p vs. {index} over the same {n} trading days", "ja-JP": "同じ{n}取引日の{index}比{pp}%p",
                   "zh-CN": "同期{n}个交易日相对{index}{pp}%p"},
    "macd_up": {"en-US": "MACD above the signal line", "ja-JP": "MACDがシグナルの上", "zh-CN": "MACD位于信号线上方"},
    "macd_down": {"en-US": "MACD below the signal line", "ja-JP": "MACDがシグナルの下", "zh-CN": "MACD位于信号线下方"},
    "macd_eq": {"en-US": "MACD equal to the signal line", "ja-JP": "MACDがシグナルと同じ", "zh-CN": "MACD与信号线持平"},
    "s_macd": {"en-US": "{name} (MACD {m}, signal {s}{caveat})", "ja-JP": "{name}（MACD {m}、シグナル {s}{caveat}）",
               "zh-CN": "{name}（MACD {m}，信号线 {s}{caveat}）"},
    "macd_below0": {"en-US": ", though MACD is below 0", "ja-JP": "、ただしMACDは0未満", "zh-CN": "，但MACD低于0"},
    "macd_above0": {"en-US": ", though MACD is above 0", "ja-JP": "、ただしMACDは0超", "zh-CN": "，但MACD高于0"},
    "rsi_up": {"en-US": "RSI(14) {v}, oversold zone", "ja-JP": "RSI(14) {v}、売られ過ぎ圏", "zh-CN": "RSI(14) {v}，超卖区间"},
    "rsi_down": {"en-US": "RSI(14) {v}, overbought zone", "ja-JP": "RSI(14) {v}、買われ過ぎ圏", "zh-CN": "RSI(14) {v}，超买区间"},
    "rsi_mid": {"en-US": "RSI(14) {v}, middle zone", "ja-JP": "RSI(14) {v}、中間圏", "zh-CN": "RSI(14) {v}，中间区间"},
    "vol_up": {"en-US": "rise on heavy volume", "ja-JP": "出来高を伴う上昇", "zh-CN": "放量上涨"},
    "vol_down": {"en-US": "decline on heavy volume", "ja-JP": "出来高を伴う下落", "zh-CN": "放量下跌"},
    "vol_none": {"en-US": "no unusual volume", "ja-JP": "出来高に特異点なし", "zh-CN": "成交量无异常"},
    "s_volume": {"en-US": "{name} ({day} on the base date; volume {ratio}x the prior {n}-trading-day average)",
                 "ja-JP": "{name}（基準日当日{day}、出来高は直前{n}取引日平均の{ratio}倍）",
                 "zh-CN": "{name}（基准日单日{day}，成交量为前{n}个交易日均值的{ratio}倍）"},
    # 줄
    "marker": {"en-US": "- {name}({ticker}), analysis period {period} ({days} trading days): {label}",
               "ja-JP": "- {name}({ticker})、分析期間{period}（{days}取引日）: {label}",
               "zh-CN": "- {name}({ticker})，分析期间{period}（{days}个交易日）：{label}"},
    "bt_hold": {"en-US": "Judgment withheld is not covered by the historical backtest.", "ja-JP": "判断保留は過去検証の対象ではありません。",
                "zh-CN": "暂不判断不属于历史回测对象。"},
    "bt_none": {"en-US": "Not shown because the results file is missing.", "ja-JP": "結果ファイルがないため表示しません。",
                "zh-CN": "因缺少结果文件，不予显示。"},
    "bt_line": {"en-US": "(signal dates in the validation window {first}~{last}, {h} trading days later): with the same label ({label}), "
                         "the share of rises was {up} (all days in the same window: {base}; sample {n}, difference {diff}, 95% interval "
                         "{lo}~{hi}). This is a historically observed frequency, not a probability or a forecast.",
                "ja-JP": "（検証期間のシグナル日{first}~{last}、{h}取引日後）: 同じラベル（{label}）のときの上昇比率は{up}"
                         "（同期間全体{base}、サンプル{n}、差{diff}、95%区間{lo}~{hi}）。過去に観測された頻度であり、確率や予測ではありません。",
                "zh-CN": "（验证区间信号日{first}~{last}，{h}个交易日后）：相同标签（{label}）时的上涨比例为{up}"
                         "（同期整体{base}，样本{n}，差值{diff}，95%区间{lo}~{hi}）。这是过去观测到的频率，并非概率或预测。"},
    "bt_flat": {"en-US": " In the backtest, this signal combination showed no clear difference from the overall average.",
                "ja-JP": "過去検証では、このシグナルの組み合わせは全体平均と明確な差がありませんでした。",
                "zh-CN": "历史回测中，该信号组合与整体平均没有明显差异。"},
    "power_brief": {"en-US": " The directional power of this signal combination was not confirmed.",
                    "ja-JP": "このシグナルの組み合わせの方向予測力は確認されませんでした。", "zh-CN": "该信号组合的方向预测力未得到证实。"},
    "b_support": {"en-US": "- Upside evidence: {x}", "ja-JP": "- 上昇の根拠: {x}", "zh-CN": "- 上涨依据：{x}"},
    "b_risk": {"en-US": "- Downside risk: {x}", "ja-JP": "- 下落リスク: {x}", "zh-CN": "- 下跌风险：{x}"},
    "b_backtest": {"en-US": "- Historical backtest {x}", "ja-JP": "- 過去検証{x}", "zh-CN": "- 历史回测{x}"},
    "b_backtest_plain": {"en-US": "- Historical backtest: {x}", "ja-JP": "- 過去検証: {x}", "zh-CN": "- 历史回测：{x}"},
    "b_phase": {"en-US": "- Market phase: {state} · Base date (latest trading day): {base}, answer generated {at} KST{warn}",
                "ja-JP": "- 市場局面: {state} · 基準日（最新取引日）: {base}、回答作成 {at} KST{warn}",
                "zh-CN": "- 市场阶段：{state} · 基准日（最新交易日）：{base}，回答生成于 {at} KST{warn}"},
    "b_warn": {"en-US": " · Data warning: {w}", "ja-JP": " · データ警告: {w}", "zh-CN": " · 数据警告：{w}"},
    "b_close": {"en-US": "- This summarizes past price signals and is not investment advice. To see technical indicators, news, "
                         "filings and earnings, ask for a detailed analysis (for example, \"자세히 분석해줘\").",
                "ja-JP": "- 過去の株価シグナルを要約したもので、投資の勧誘ではありません。テクニカル指標・ニュース・開示・業績まで見るには、"
                         "詳しい分析を依頼してください（例: 「자세히 분석해줘」）。",
                "zh-CN": "- 本内容是对过去股价信号的总结，不构成投资建议。如需查看技术指标、新闻、公告和业绩，请要求详细分析"
                         "（例如：“자세히 분석해줘”）。"},
    # 데이터 경고
    "w_lag1": {"en-US": "The latest price ({base}) is 1 business day older than the expected trading day ({exp}){why}. "
                        "This analysis is based on the {base} close and is not an outlook for the latest trading day.",
               "ja-JP": "最新の株価（{base}）は想定取引日（{exp}）より1営業日前のデータです{why}。"
                        "この分析は{base}終値基準で、最新取引日の見通しではありません。",
               "zh-CN": "最新股价（{base}）比预期交易日（{exp}）早1个工作日{why}。本分析以{base}收盘价为准，并非最新交易日的展望。"},
    "w_lag": {"en-US": "The latest price ({base}) is {lag} business days behind the expected trading day ({exp}){why}. "
                       "This analysis is based on the {base} close and is not an outlook for the latest trading day.",
              "ja-JP": "最新の株価（{base}）は想定取引日（{exp}）より{lag}営業日遅れたデータです{why}。"
                       "この分析は{base}終値基準で、最新取引日の見通しではありません。",
              "zh-CN": "最新股价（{base}）比预期交易日（{exp}）滞后{lag}个工作日{why}。本分析以{base}收盘价为准，并非最新交易日的展望。"},
    "w_lag_over": {"en-US": "The latest price ({base}) is {lag} business days behind the expected trading day ({exp}) "
                            "(more than 5 business days){why}, so there is not enough data. Judgment is withheld.",
                   "ja-JP": "最新の株価（{base}）は想定取引日（{exp}）より{lag}営業日遅れており（5営業日超）{why}、データが不足しています。"
                            "判断を保留します。",
                   "zh-CN": "最新股价（{base}）比预期交易日（{exp}）滞后{lag}个工作日（超过5个工作日）{why}，数据不足。暂不判断。"},
    "w_missing": {"en-US": "Insufficient price data: prices are missing for {n} trading days in the calculation window that exist in "
                           "the index. Judgment is withheld.",
                  "ja-JP": "株価データ不足: 計算期間のうち、指数にはある取引日{n}日分の株価がありません。判断を保留します。",
                  "zh-CN": "股价数据不足：计算区间内有{n}个指数有数据的交易日没有该股股价。暂不判断。"},
    "w_not_trading": {"en-US": "The base date ({base}) is not a KOSPI index trading day, so it could not be confirmed as an actual "
                               "trading day.",
                      "ja-JP": "基準日（{base}）がKOSPI指数の取引日にないため、実際の取引日か確認できませんでした。",
                      "zh-CN": "基准日（{base}）不在KOSPI指数交易日中，无法确认是否为实际交易日。"},
    "why_status": {"en-US": " ({status})", "ja-JP": "（{status}）", "zh-CN": "（{status}）"},
    # 시세 신선도(후속 답 머리말)
    "f_latest": {"en-US": "latest prices", "ja-JP": "最新の株価", "zh-CN": "最新股价"},
    "f_lag1": {"en-US": "prices 1 business day old{cause}", "ja-JP": "1営業日前の株価{cause}", "zh-CN": "1个工作日前的股价{cause}"},
    "f_lag": {"en-US": "{lag} business days behind{cause}", "ja-JP": "{lag}営業日遅れ{cause}", "zh-CN": "滞后{lag}个工作日{cause}"},
    "f_over": {"en-US": "more than 5 business days behind{cause}", "ja-JP": "5営業日超の遅れ{cause}", "zh-CN": "滞后超过5个工作日{cause}"},
    "f_missing": {"en-US": "insufficient price data", "ja-JP": "株価データ不足", "zh-CN": "股价数据不足"},
    "stale": {"en-US": " · Prices: {fresh}, based on the {base} close", "ja-JP": " · 株価: {fresh}、{base}終値基準",
              "zh-CN": " · 股价：{fresh}，以{base}收盘价为准"},
    # 후속 답
    "f_reasons": {"en-US": "- Evidence: {up} / Risk: {down}", "ja-JP": "- 根拠: {up} / リスク: {down}", "zh-CN": "- 依据：{up} / 风险：{down}"},
    "f_power": {"en-US": "- Historical backtest: {x}", "ja-JP": "- 過去検証: {x}", "zh-CN": "- 历史回测：{x}"},
    "f_level": {"en-US": "- Price levels to watch: {x}", "ja-JP": "- 確認すべき価格帯: {x}", "zh-CN": "- 需关注的价位：{x}"},
    "p_hold": {"en-US": "Judgment withheld is not covered by the historical backtest, so its predictive power cannot be stated.",
               "ja-JP": "判断保留は過去検証の対象ではないため、予測力については言えません。", "zh-CN": "暂不判断不属于历史回测对象，因此无法说明其预测力。"},
    "p_none": {"en-US": "There are no backtest results yet, so its predictive power cannot be stated.",
               "ja-JP": "過去検証の結果がまだないため、予測力については言えません。", "zh-CN": "尚无历史回测结果，因此无法说明其预测力。"},
    "p_head": {"en-US": "In the historical backtest, the share of rises {period} after this signal state ({label}) was {up}, "
                        "versus {base} for all days in the same window ({stats}).",
               "ja-JP": "過去検証では、このシグナル状態（{label}）の{period}後の上昇比率は{up}、同期間全体は{base}でした（{stats}）。",
               "zh-CN": "历史回测中，该信号状态（{label}）在{period}后的上涨比例为{up}，同期整体为{base}（{stats}）。"},
    "p_sig": {"en-US": " The difference was statistically significant, but it is only a historical frequency, not a probability or a forecast.",
              "ja-JP": "差は統計的に有意でしたが、過去の頻度にすぎず、確率や予測ではありません。",
              "zh-CN": "差异具有统计显著性，但这只是过去的频率，并非概率或预测。"},
    "p_nosig": {"en-US": " The difference was not statistically significant, so directional predictive power was not confirmed.",
                "ja-JP": "差は統計的に有意ではなく、方向予測力は確認されませんでした。", "zh-CN": "差异不具有统计显著性，方向预测力未得到证实。"},
    "stats": {"en-US": "sample {n}, difference {diff}, 95% interval {lo}~{hi}", "ja-JP": "サンプル{n}、差{diff}、95%区間{lo}~{hi}",
              "zh-CN": "样本{n}，差值{diff}，95%区间{lo}~{hi}"},
    "lvl_recover": {"en-US": "whether it recovers {x}", "ja-JP": "{x}を回復するか", "zh-CN": "能否收复{x}"},
    "lvl_support": {"en-US": "whether {x} holds as support", "ja-JP": "{x}で支えられるか", "zh-CN": "能否守住{x}"},
    "lvl_ma": {"en-US": "the {n}-day MA {won}", "ja-JP": "{n}日線{won}", "zh-CN": "{n}日均线{won}"},
    "lvl_sep": {"en-US": " and ", "ja-JP": "・", "zh-CN": "、"},
    "lvl_low": {"en-US": "the lowest close in the analysis period, {won} ({d})", "ja-JP": "分析期間の最安終値{won}（{d}）",
                "zh-CN": "分析期间最低收盘价{won}（{d}）"},
    "why_up": {"en-US": "upward groups: {n} ({names})", "ja-JP": "上昇側のシグナル群が{n}つ（{names}）", "zh-CN": "上涨方向信号组{n}个（{names}）"},
    "why_no_up": {"en-US": "no upward groups", "ja-JP": "上昇側のシグナル群はなく", "zh-CN": "没有上涨方向的信号组"},
    "why_down": {"en-US": "downward groups: {n} ({names})", "ja-JP": "下落側のシグナル群が{n}つ（{names}）", "zh-CN": "下跌方向信号组{n}个（{names}）"},
    "why_no_down": {"en-US": "no downward groups", "ja-JP": "下落側のシグナル群はなく", "zh-CN": "没有下跌方向的信号组"},
    "why_hold": {"en-US": "{first} The reason is that {reason} ({tally}).", "ja-JP": "{first}そう判断した理由は、{reason}ためです（{tally}）。",
                 "zh-CN": "{first}这样判断的原因是{reason}（{tally}）。"},
    "why_reach": {"en-US": "reaches", "ja-JP": "に達する", "zh-CN": "达到"},
    "why_short": {"en-US": "falls short of", "ja-JP": "に届かない", "zh-CN": "未达到"},
    "why_main": {"en-US": "{first} The reason: {up}, {down}, neutral: {neutral}, so the gap of {gap} between up and down "
                          "groups {reach} the threshold ({margin}) ({tally}).",
                 "ja-JP": "{first}そう判断した理由は、{up}、{down}、中立が{neutral}つで、上昇・下落のシグナル群の差{gap}つが判定基準（{margin}つ）"
                          "{reach}ためです（{tally}）。",
                 "zh-CN": "{first}这样判断的原因是：{up}，{down}，中性{neutral}个，上涨与下跌信号组的差距{gap}个{reach}判定标准（{margin}个）"
                          "（{tally}）。"},
    "why_trend": {"en-US": "- Trend (considered separately): currently {regime}. The trend is not used in the directional judgment.",
                  "ja-JP": "- トレンド（別途参照）: 現在は{regime}です。トレンドは方向判定に含めません。",
                  "zh-CN": "- 趋势（单独参考）：目前为{regime}。趋势不计入方向判定。"},
    "one_dir": {"en-US": "{d} ({tally}{weak}, confidence: {conf}).", "ja-JP": "{d}です（{tally}{weak}、信頼度: {conf}）。",
                "zh-CN": "{d}（{tally}{weak}，可信度：{conf}）。"},
    "one_weak": {"en-US": ", weak signal", "ja-JP": "、弱いシグナル", "zh-CN": "，信号较弱"},
    "one_neutral": {"en-US": "Right now neither the upside nor the downside clearly dominates ({tally}).",
                    "ja-JP": "現時点では上昇と下落のどちらも明確に優勢ではありません（{tally}）。", "zh-CN": "目前上涨和下跌均无明显优势（{tally}）。"},
    "compare_up": {"en-US": "By group count, there is more upside evidence", "ja-JP": "シグナル群の票では上昇の根拠のほうが多いです",
                   "zh-CN": "按信号组计票，上涨依据更多"},
    "compare_down": {"en-US": "By group count, there is more downside risk", "ja-JP": "シグナル群の票では下落リスクのほうが多いです",
                     "zh-CN": "按信号组计票，下跌风险更多"},
    "compare_even": {"en-US": "By group count, upside and downside are about even", "ja-JP": "シグナル群の票では上昇と下落がほぼ同じです",
                     "zh-CN": "按信号组计票，上涨与下跌大致相当"},
    "compare_line": {"en-US": "- {c} ({tally}); the market phase is '{regime}'.", "ja-JP": "- {c}（{tally}）。局面は「{regime}」です。",
                     "zh-CN": "- {c}（{tally}），市场阶段为“{regime}”。"},
    "buy_line": {"en-US": "- I do not recommend buying or selling. This direction reflects the current signal state and does not guarantee "
                          "future returns.",
                 "ja-JP": "- 売買の可否はお勧めしません。この方向は現在のシグナル状態であり、将来の収益を保証するものではありません。",
                 "zh-CN": "- 不提供买卖建议。该方向只是当前的信号状态，不保证未来收益。"},
    "prob_none": {"en-US": "- There is no verified probability of a rise or fall, so the likelihood is not stated as a number.",
                  "ja-JP": "- 検証された上昇・下落の確率はないため、可能性を数値では示しません。",
                  "zh-CN": "- 没有经过验证的上涨或下跌概率，因此不以数字表示可能性。"},
    "prob_freq": {"en-US": "- Historical frequency: in the validation window ({first}~{last}), on days with the same signal state as now "
                           "({label}), the share of rises {h} trading days later was {up} in the past, versus {base} for all days in the "
                           "same window ({stats}). This is a historically observed frequency, not a future probability.",
                  "ja-JP": "- 過去の頻度: 検証期間（{first}~{last}）で現在と同じシグナル状態（{label}）だった日の{h}取引日後の上昇比率は"
                           "過去{up}、同期間全体は{base}でした（{stats}）。過去に観測された頻度であり、将来の確率ではありません。",
                  "zh-CN": "- 过去的频率：在验证区间（{first}~{last}）中，与当前信号状态相同（{label}）的交易日，{h}个交易日后的上涨比例"
                           "过去为{up}，同期整体为{base}（{stats}）。这是过去观测到的频率，并非未来概率。"},
    "prob_nofreq": {"en-US": "- Historical frequency: there are no backtest results for this signal state.",
                    "ja-JP": "- 過去の頻度: このシグナル状態の過去検証結果はありません。", "zh-CN": "- 过去的频率：该信号状态没有历史回测结果。"},
    "risk_line": {"en-US": "- Downside risk: {x}", "ja-JP": "- 下落リスク: {x}", "zh-CN": "- 下跌风险：{x}"},
    "risk_mixed": {"en-US": "- Downside risk (mixed group): {x}", "ja-JP": "- 下落リスク（相反するシグナル群）: {x}",
                   "zh-CN": "- 下跌风险（方向不一的信号组）：{x}"},
    "risk_context": {"en-US": "- Downside risk (trend reference, outside the signal table): {x}",
                     "ja-JP": "- 下落リスク（トレンド参考、シグナル判定表の外）: {x}", "zh-CN": "- 下跌风险（趋势参考，不计入信号判定表）：{x}"},
    "risk_ma20_up": {"en-US": "the 20-day MA {won} (close is above)", "ja-JP": "20日線{won}（終値はその上）", "zh-CN": "20日均线{won}（收盘价在其上方）"},
    "risk_ma20_down": {"en-US": "the 20-day MA {won} (close is below)", "ja-JP": "20日線{won}（終値はその下）",
                       "zh-CN": "20日均线{won}（收盘价在其下方）"},
    "risk_low": {"en-US": "the lowest close in the analysis period, {won} ({d}, based on the last {n} trading days)",
                 "ja-JP": "分析期間の最安終値{won}（{d}、直近{n}取引日基準）", "zh-CN": "分析期间最低收盘价{won}（{d}，以近{n}个交易日为准）"},
    "risk_low52": {"en-US": "the 52-week lowest close, {won} ({d}, based on the last {n} trading days)",
                   "ja-JP": "52週最安終値{won}（{d}、直近{n}取引日基準）", "zh-CN": "52周最低收盘价{won}（{d}，以近{n}个交易日为准）"},
    "risk_levels": {"en-US": "- Price levels to watch: {x}", "ja-JP": "- 確認すべき価格帯: {x}", "zh-CN": "- 需关注的价位：{x}"},
    "list_sep": {"en-US": ", ", "ja-JP": "、", "zh-CN": "，"},
    "data_intro": {"en-US": "This answer was generated at {at} KST, and the price base date is the {base} close.",
                   "ja-JP": "この回答は{at} KSTに作成し、株価の基準日は{base}の終値です。",
                   "zh-CN": "本回答生成于{at} KST，股价基准日为{base}收盘价。"},
    "data_latest": {"en-US": "- Current status: prices up to the expected trading day {exp} are included (latest).",
                    "ja-JP": "- 現在の状態: 想定取引日{exp}の株価まで反映されています（最新）。",
                    "zh-CN": "- 当前状态：已包含截至预期交易日{exp}的股价（最新）。"},
    "data_stale": {"en-US": "- Current status: there is no price for the expected trading day {exp} ({status}). So this judgment is based "
                            "on the {base} close and is not an outlook for the latest trading day.",
                   "ja-JP": "- 現在の状態: 想定取引日{exp}の株価がありません（{status}）。そのため、この判断は{base}終値基準で、"
                            "最新取引日の見通しではありません。",
                   "zh-CN": "- 当前状态：没有预期交易日{exp}的股价（{status}）。因此本判断以{base}收盘价为准，并非最新交易日的展望。"},
    "data_policy": {"en-US": "- How we answer: if prices are 1 to 5 business days late, we state the base date and the reason "
                             "(not collected, collection failed, presumed market holiday, missing for this stock only) and do not call "
                             "it the latest outlook. If they are more than {max} business days late or trading days are missing in the "
                             "calculation window, we withhold judgment. Because there is no KRX holiday calendar, holidays are inferred "
                             "from collection records.",
                    "ja-JP": "- 回答の方法: 株価が1~5営業日遅れている場合は基準日と遅れの理由（未収集・収集失敗・休場日と推定・この銘柄のみ欠落）を"
                             "示し、最新の見通しとは言いません。{max}営業日を超えて遅れているか、計算期間に欠けた取引日がある場合は判断を保留します。"
                             "KRXの休場日カレンダーがないため、休場日は収集記録から推定します。",
                    "zh-CN": "- 回答方式：若股价滞后1~5个工作日，会说明基准日和滞后原因（未采集、采集失败、推定为休市日、仅该股票缺失），"
                             "且不称其为最新展望。若滞后超过{max}个工作日或计算区间内缺少交易日，则暂不判断。由于没有KRX休市日历，"
                             "休市日根据采集记录推定。"},
    "data_dir": {"en-US": "- Direction: {x}", "ja-JP": "- 方向: {x}", "zh-CN": "- 方向：{x}"},
}


def _t(key: str, locale: str, **kw) -> str:
    return T[key][locale].format(**kw)


def _w(table: dict, key: str, locale: str) -> str:
    return table[key][locale]


# ---------- 숫자 표기(값은 app.outlook과 같은 자리수) ----------


def _won(v: float, locale: str) -> str:
    num = f"{v:,.0f}" if float(v).is_integer() else f"{v:,.2f}"
    return {"en-US": f"KRW {num}", "ja-JP": f"{num}ウォン", "zh-CN": f"{num}韩元"}[locale]


def _signed(v: float) -> str:
    return f"{v:+.2f}%"


def _pp(v: float) -> str:
    return f"{v * 100:+.1f}%p"


def _rate(v: float) -> str:
    return f"{v * 100:.1f}%"


# ---------- 문장 ----------


class _Ctx:
    """한 답변에 필요한 값(계산은 app.outlook 그대로)."""

    def __init__(self, company, period: o.Period, res: dict, locale: str):
        self.c, self.p, self.res, self.L = company, period, res, locale
        self.ind, self.label, self.data = res["indicators"], res["label"], res["data"]
        self.regime = o.classify_regime(self.ind, self.label)
        self.d, self.conf, self.t = o._direction(res, period)

    def tally(self) -> str:
        return _t("tally", self.L, **self.t)

    def title(self) -> str:
        return _t("title", self.L, name=self.c.name, ticker=self.c.ticker, days=self.p.days)

    def dir_title(self) -> str:
        out = _t("dir_title", self.L, title=self.title(), d=_w(_DIR, self.d, self.L))
        return out + (_t("dir_conf", self.L, conf=_w(_CONF, self.conf, self.L)) if self.conf else "")

    def state(self) -> str:
        return _t("state", self.L, regime=_w(_REGIME, self.regime, self.L), base=self.ind["base_date"])

    def hold_reason(self) -> str:
        if self.data.get("lag_business_days", 0) > o.MAX_LAG_BUSINESS_DAYS:
            return _t("hold_lag", self.L, lag=self.data["lag_business_days"])
        if self.data.get("missing_days"):
            return _t("hold_missing", self.L, n=self.data["missing_days"])
        return _t("hold_groups", self.L, total=self.t["total"], min=o.MIN_GROUPS)

    def direction_sentence(self, pick: bool = False) -> str:
        L, t = self.L, self.t
        base = self.ind["base_date"]
        when = _t("when", L, base=base) if (self.data.get("lag_business_days") or 0) >= 1 else ""
        if self.d == o.DIR_HOLD:
            s = _t("hold_sentence", L, reason=self.hold_reason(), tally=self.tally())
            return (_t("pick_hold", L) + s) if pick else _cap(s, L)
        if self.d == o.DIR_NEUTRAL:
            gap = t["up"] - t["down"]
            more = (_t("neutral_gap", L, side=_t("side_up" if gap > 0 else "side_down", L), gap=abs(gap), margin=o.GROUP_MARGIN)
                    if gap else "")
            s = _t("neutral_sentence", L, tally=self.tally(), more=more, regime=_w(_REGIME_SHORT, self.regime, L), when=when)
            return (_t("pick_hold", L) + s) if pick else _cap(s, L)
        lead = t["up"] if self.d == o.DIR_UP else t["down"]
        strength = _t("weak", L, tally=self.tally()) if lead * 2 < t["total"] else _t("share", L, tally=self.tally())
        regime_s = _w(_REGIME_SHORT, self.regime, L)
        trend = (_t("trend_same", L, regime=regime_s) if o._REGIME_SIDE.get(self.regime) == self.d
                 else _t("trend_other", L, regime=regime_s))
        r = o._backtest_row(self.p, self.label)
        why = (_t("why_conf_unproven", L) if self.conf == o.CONF_LOW and r is not None
               else _t("why_conf_none", L) if self.conf == o.CONF_LOW else "")
        conf = _t("conf_word", L, conf=_w(_CONF, self.conf, L))
        d = _w(_DIR, self.d, L)
        if L == "en-US":
            d = _DIR_SENT_EN[self.d] if pick else _cap(_DIR_SENT_EN[self.d], L)
        return _t("dir_sentence", L, pick=_t("pick", L) if pick else "", d=d, strength=strength, trend=trend, why=why,
                  conf=conf, when=when)

    def core_sentence(self) -> str:
        """"왜?" 답의 첫 문장(방향만)."""
        if self.d == o.DIR_HOLD:
            return _t("core_hold", self.L)
        if self.d == o.DIR_NEUTRAL:
            return _t("core_neutral", self.L)
        d = _w(_DIR, self.d, self.L)
        return _t("core_dir", self.L, pick="", d=_cap(_DIR_SENT_EN[self.d], self.L) if self.L == "en-US" else d)

    # --- 근거(app.outlook.group_evidence와 같은 원천·같은 순서) ---

    def signal_text(self, s: dict, by_kind: dict) -> str:
        L, ind, p = self.L, self.ind, self.p
        k = s["kind"]
        if k == "period_return":
            r = ind["returns"][p.days]
            return _t("s_return", L, n=p.days, pct=_signed(r["pct"]), d=r["from_date"])
        if k in ("close_vs_ma_short", "close_vs_ma_long"):
            n = p.ma[0] if k == "close_vs_ma_short" else p.ma[1]
            key = "s_close_ma_up" if s["side"] == o.UP else "s_close_ma_down"
            return _t(key, L, close=_won(ind["close"], L), n=n, ma=_won(round(ind[f"ma{n}"]["value"]), L))
        if k == "ma_alignment":
            short, long = p.ma
            c = by_kind.get("cross")
            cross = (_t("cross_sep", L) + self.cross_name(c)) if c and c["side"] == s["side"] else ""
            key = "s_align_up" if s["side"] == o.UP else "s_align_down"
            return _t(key, L, s=short, v1=_won(round(ind[f"ma{short}"]["value"]), L), l=long,
                      v2=_won(round(ind[f"ma{long}"]["value"]), L), cross=cross)
        if k == "relative":
            rel = ind["relative"]
            return _t("s_relative", L, n=rel["period_days"], index=_INDEX.get(rel["index_name"], rel["index_name"]),
                      pp=f"{rel['diff_pct_point']:+.2f}")
        if k == "macd":
            m = ind["macd"]
            name = _t({o.UP: "macd_up", o.DOWN: "macd_down"}.get(s["side"], "macd_eq"), L)
            caveat = (_t("macd_below0", L) if s["side"] == o.UP and m["macd"] < 0
                      else _t("macd_above0", L) if s["side"] == o.DOWN and m["macd"] > 0 else "")
            return _t("s_macd", L, name=name, m=f"{m['macd']:,.2f}", s=f"{m['signal']:,.2f}", caveat=caveat)
        if k == "rsi":
            key = "rsi_up" if s["side"] == o.UP else "rsi_down" if s["side"] == o.DOWN else "rsi_mid"
            return _t(key, L, v=f"{ind['rsi']:.2f}")
        if k == "volume" and ind.get("volume"):
            name = _t({o.UP: "vol_up", o.DOWN: "vol_down"}.get(s["side"], "vol_none"), L)
            return _t("s_volume", L, name=name, day=_signed(ind.get("day_change_pct") or 0), n=o.VOLUME_AVG_DAYS,
                      ratio=f"{ind['volume']['ratio']:.2f}")
        if k == "cross":
            return self.cross_name(s)
        raise KeyError(k)  # 모르는 신호 종류: 템플릿을 쓰지 않는다

    def cross_name(self, s: dict) -> str:
        if s["side"] == o.UP:
            return _t("golden", self.L, d=s["value"])
        if s["side"] == o.DOWN:
            return _t("dead", self.L, d=s["value"])
        return _t("no_cross", self.L, n=self.p.days)

    def evidence(self) -> dict[str, list[str]]:
        L, ind, signals, p = self.L, self.ind, self.res["signals"], self.p
        votes = o.group_votes(signals)
        by_kind = {s["kind"]: s for s in signals}
        out: dict[str, list[str]] = {"up": [], "down": [], "mixed": [], "context": []}
        for g, vote in votes.items():
            members = [s for s in signals if s["group"] == g and not o.counted_elsewhere(s, signals)]
            ups = [self.signal_text(s, by_kind) for s in members if s["side"] == o.UP]
            downs = [self.signal_text(s, by_kind) for s in members if s["side"] == o.DOWN]
            gname = _w(_GROUP, g, L)
            if vote == o.UP and ups:
                out["up"].append(f"[{gname}] {'; '.join(ups)}" + (_t("opposite", L, x="; ".join(downs)) if downs else ""))
            elif vote == o.DOWN and downs:
                out["down"].append(f"[{gname}] {'; '.join(downs)}" + (_t("opposite", L, x="; ".join(ups)) if ups else ""))
            elif vote == o.NEUTRAL and ups and downs:
                out["mixed"].append(_t("mixed", L, g=gname, ups="; ".join(ups), downs="; ".join(downs)))
        ri = ind.get("regime_inputs") or {}
        for n, threshold in ((20, o.REGIME_TREND20_PCT), (60, o.REGIME_TREND60_PCT)):
            if n == p.days or ri.get(f"r{n}") is None or abs(ri[f"r{n}"]) < threshold:
                continue
            d, c = ri[f"r{n}_from"]
            out["context"].append(_t("ctx_return", L, n=n, pct=_signed(ri[f"r{n}"]), d=d, won=_won(c, L)))
        if 60 not in p.ma and ri.get("ma60") is not None:
            key = "ctx_ma60_up" if ind["close"] > ri["ma60"] else "ctx_ma60_down"
            out["context"].append(_t(key, L, won=_won(round(ri["ma60"]), L)))
        day = ind.get("day_change_pct")
        if "volume" not in by_kind and day is not None and abs(day) >= o.BIG_DAY_MOVE_PCT:
            out["context"].append(_t("ctx_day", L, pct=_signed(day), base=ind["base_date"]))
        return out

    def support_text(self, ge: dict, n: int = 2) -> str:
        return "; ".join(ge["up"][:n]) if ge["up"] else _t("none_up", self.L)

    def risk_text(self, ge: dict, n: int = 2) -> str:
        L = self.L
        head = "; ".join(ge["down"][:n]) if ge["down"] else _t("none_down", L)
        extra = []
        if ge["mixed"]:
            extra.append(_t("mixed_label", L, x=ge["mixed"][0]))
        if ge["context"]:
            extra.append(_t("context_label", L, x="; ".join(ge["context"][:2])))
        if not extra:
            return head
        stop = "." if L == "en-US" else "。"
        sep = " " if L == "en-US" else ""
        end = lambda s: s if s.endswith((".", "。")) else s + stop  # noqa: E731
        return sep.join([end(head)] + [end(x) for x in extra])

    # --- 과거 검증 ---

    def stats(self, r: dict) -> str:
        lo, hi = r["diff_ci95"]
        return _t("stats", self.L, n=f"{r['n']:,}", diff=_pp(r["diff"]), lo=f"{lo * 100:+.1f}", hi=f"{hi * 100:+.1f}%p")

    def backtest_body(self) -> tuple[str, bool]:
        """(본문, 콜론을 붙여야 하는지). app.outlook.backtest_line과 같은 조건."""
        L, label = self.L, self.label
        if label == o.LABEL_HOLD:
            return _t("bt_hold", L), True
        v = ((o.backtest_summary() or {}).get("periods", {}).get(self.p.label, {}).get("valid") or {})
        r = (v.get("labels") or {}).get(label)
        if not r or not r.get("n"):
            return _t("bt_none", L), True
        lo, hi = r["diff_ci95"]
        line = _t("bt_line", L, first=v["first_date"], last=v["last_date"], h=v["horizon"], label=_w(_LABEL, label, L),
                  up=_rate(r["up_rate"]), base=_rate(r["base_rate"]), n=f"{r['n']:,}", diff=_pp(r["diff"]),
                  lo=f"{lo * 100:+.1f}", hi=f"{hi * 100:+.1f}%p")
        if not r.get("significant"):
            line += _t("bt_flat", L)
        return line, False

    def power_sentence(self) -> str:
        L, label = self.L, self.label
        if label == o.LABEL_HOLD:
            return _t("p_hold", L)
        r = o._backtest_row(self.p, label)
        if r is None:
            return _t("p_none", L)
        head = _t("p_head", L, period=_w(_PERIOD, self.p.label, L), label=_w(_LABEL, label, L), up=_rate(r["up_rate"]),
                  base=_rate(r["base_rate"]), stats=self.stats(r))
        return head + (_t("p_sig", L) if r.get("significant") else _t("p_nosig", L))

    # --- 데이터 경고·신선도(app.outlook.data_check·analyze와 같은 조건) ---

    def status_word(self) -> str | None:
        st = self.data.get("status")
        return _PRICE_STATUS[st][self.L] if st in _PRICE_STATUS else None

    def warnings(self) -> list[str] | None:
        L, data = self.L, self.data
        lag = data.get("lag_business_days") or 0
        base, exp = self.ind["base_date"], data["expected_date"]
        st = self.status_word()
        why = _t("why_status", L, status=st) if st else ""
        out = []
        if lag == 1:
            out.append(_t("w_lag1", L, base=base, exp=exp, why=why))
        elif 2 <= lag <= o.MAX_LAG_BUSINESS_DAYS:
            out.append(_t("w_lag", L, base=base, exp=exp, lag=lag, why=why))
        elif lag > o.MAX_LAG_BUSINESS_DAYS:
            out.append(_t("w_lag_over", L, base=base, exp=exp, lag=lag, why=why))
        if data.get("missing_days"):
            out.append(_t("w_missing", L, n=data["missing_days"]))
        if data.get("base_is_trading_day") is False:
            out.append(_t("w_not_trading", L, base=base))
        # 한국어 경고와 개수가 다르면(모르는 경고가 있다) 템플릿을 쓰지 않는다
        return out if len(out) == len(data.get("warnings") or []) else None

    def freshness(self) -> str:
        L, data = self.L, self.data
        if data.get("missing_days"):
            return _t("f_missing", L)
        lag = data.get("lag_business_days") or 0
        st = self.status_word()
        cause = _t("why_status", L, status=st) if st else ""
        if lag <= 0:
            return _t("f_latest", L)
        if lag == 1:
            return _t("f_lag1", L, cause=cause)
        if lag <= o.MAX_LAG_BUSINESS_DAYS:
            return _t("f_lag", L, lag=lag, cause=cause)
        return _t("f_over", L, cause=cause)

    def marker(self) -> str:
        return _t("marker", self.L, name=self.c.name, ticker=self.c.ticker, period=_w(_PERIOD, self.p.label, self.L),
                  days=self.p.days, label=_w(_LABEL, self.label, self.L))

    def level_line(self) -> str:
        L, ind = self.L, self.ind
        mas = o._ma_values(ind, self.p)
        above = [n for n, v in mas.items() if v > ind["close"]]
        below = [n for n, v in mas.items() if v < ind["close"]]
        sep = _t("lvl_sep", L)
        if above:
            return _t("lvl_recover", L, x=sep.join(_t("lvl_ma", L, n=n, won=_won(round(mas[n]), L)) for n in above))
        if below:
            return _t("lvl_support", L, x=sep.join(_t("lvl_ma", L, n=n, won=_won(round(mas[n]), L)) for n in below))
        rg = ind["range"]
        return _t("lvl_low", L, won=_won(rg["low"], L), d=rg["low_date"])


def _cap(s: str, locale: str) -> str:
    return s[:1].upper() + s[1:] if locale == "en-US" else s


# ---------- 답 ----------


def brief_answer(company, period: o.Period, res: dict, now: datetime, locale: str) -> str | None:
    """app.outlook.brief_answer와 같은 줄 구성."""
    x = _Ctx(company, period, res, locale)
    warns = x.warnings()
    if warns is None:
        return None
    ge = x.evidence()
    r = o._backtest_row(period, x.label) if x.label != o.LABEL_HOLD else None
    power = _t("power_brief", locale) if r is not None and not r.get("significant") else ""
    body, colon = x.backtest_body()
    bt = _t("b_backtest_plain", locale, x=body + power) if colon else _t("b_backtest", locale, x=body + power)
    lines = [x.dir_title(), "", x.direction_sentence(),
             _t("b_support", locale, x=x.support_text(ge)),
             _t("b_risk", locale, x=x.risk_text(ge)),
             x.marker(),
             bt,
             _t("b_phase", locale, state=x.state(), base=x.ind["base_date"], at=f"{now.astimezone(KST):%Y-%m-%d %H:%M}",
                warn="".join(_t("b_warn", locale, w=w) for w in warns)),
             _t("b_close", locale)]
    return "\n".join(lines)


def followup_answer(company, period: o.Period, res: dict, kind: str, now: datetime, locale: str) -> str | None:
    """app.outlook.followup_answer와 같은 줄 구성(earnings·filing·signal은 None)."""
    if kind not in SUPPORTED_MODES or kind == "brief":
        return None
    L = locale
    x = _Ctx(company, period, res, L)
    if x.warnings() is None:
        return None
    if kind == "oneline":
        lead = x.t["up"] if x.d == o.DIR_UP else x.t["down"] if x.d == o.DIR_DOWN else None
        if x.d in (o.DIR_UP, o.DIR_DOWN):
            weak = _t("one_weak", L) if lead * 2 < x.t["total"] else ""
            d = _w(_DIR, x.d, L)
            core = _t("one_dir", L, d=_cap(_DIR_SENT_EN[x.d], L) if L == "en-US" else d, tally=x.tally(), weak=weak,
                      conf=_w(_CONF, x.conf, L))
        elif x.d == o.DIR_NEUTRAL:
            core = _t("one_neutral", L, tally=x.tally())
        else:
            core = _cap(_t("hold_sentence", L, reason=x.hold_reason(), tally=x.tally()), L)
        stop, sp = (".", " ") if L == "en-US" else ("。", "")
        return f"{x.dir_title()} · {core}{sp}{x.state()}{stop}"
    base = x.ind["base_date"]
    fresh = x.freshness()
    stale = (_t("stale", L, fresh=fresh, base=base)
             if x.data.get("freshness") and x.data["freshness"] != "최신 시세" and kind != "data" else "")
    head = f"{x.dir_title()} · {x.state()}{stale}"
    first = x.direction_sentence(pick=(kind == "pick"))
    ge = x.evidence()
    reasons = _t("f_reasons", L, up=x.support_text(ge, 1), down=x.risk_text(ge, 1))
    power = _t("f_power", L, x=x.power_sentence())
    level = _t("f_level", L, x=x.level_line())
    if kind == "pick":
        return o._blocks([[head], [first]])
    if kind == "why":
        lines = [_t("b_support", L, x=x.support_text(ge)), _t("b_risk", L, x=x.risk_text(ge)),
                 _t("why_trend", L, regime=_w(_REGIME, x.regime, L))]
        return o._blocks([[head], [_why_sentence(x)], lines])
    if kind == "data":
        st = x.status_word()
        status_line = (_t("data_latest", L, exp=x.data["expected_date"]) if x.data.get("status") in (None, o.PRICE_STATUS_LATEST)
                       else _t("data_stale", L, exp=x.data["expected_date"], status=st or "", base=base))
        lines = [_t("data_intro", L, at=f"{now.astimezone(KST):%Y-%m-%d %H:%M}", base=base), status_line,
                 _t("data_policy", L, max=o.MAX_LAG_BUSINESS_DAYS), _t("data_dir", L, x=first)]
        return o._blocks([[head], lines[:1], lines[1:]])
    if kind == "risk":
        lines = [_t("risk_line", L, x=v) for v in ge["down"][:2]] or [_t("risk_line", L, x=_t("none_down", L))]
        if ge["mixed"]:
            lines.append(_t("risk_mixed", L, x=ge["mixed"][0]))
        if ge["context"] and len(lines) < 3:
            lines.append(_t("risk_context", L, x="; ".join(ge["context"][:2])))
        ind = x.ind
        rg, r52 = ind["range"], ind.get("range52")
        levels = []
        if (ri := ind.get("regime_inputs") or {}).get("ma20") is not None:
            key = "risk_ma20_down" if ind["close"] < ri["ma20"] else "risk_ma20_up"
            levels.append(_t(key, L, won=_won(round(ri["ma20"]), L)))
        levels.append(_t("risk_low", L, won=_won(rg["low"], L), d=rg["low_date"], n=rg["days"]))
        if r52:
            levels.append(_t("risk_low52", L, won=_won(r52["low"], L), d=r52["low_date"], n=r52["days"]))
        lines.append(_t("risk_levels", L, x=_t("list_sep", L).join(levels)))
        return o._blocks([[head], [first], lines])
    if kind == "probability":
        r = o._backtest_row(period, x.label) if x.label != o.LABEL_HOLD else None
        if r is not None:
            v = o.backtest_summary()["periods"][period.label]["valid"]
            freq = _t("prob_freq", L, first=v["first_date"], last=v["last_date"], label=_w(_LABEL, x.label, L), h=v["horizon"],
                      up=_rate(r["up_rate"]), base=_rate(r["base_rate"]), stats=x.stats(r))
        else:
            freq = _t("prob_nofreq", L)
        return o._blocks([[head], [first], [_t("prob_none", L), freq, reasons, level]])
    if kind == "compare":
        t = x.t
        c = _t("compare_up" if t["up"] > t["down"] else "compare_down" if t["down"] > t["up"] else "compare_even", L)
        return o._blocks([[head], [first], [_t("compare_line", L, c=c, tally=x.tally(), regime=_w(_REGIME, x.regime, L)),
                                            reasons, power]])
    if kind == "buy":
        return o._blocks([[head], [first], [_t("buy_line", L), reasons, power]])
    return o._blocks([[head], [first], [reasons, power]])


def _why_sentence(x: _Ctx) -> str:
    L = x.L
    ups = [g for g, v in o.group_votes(x.res["signals"]).items() if v == o.UP]
    downs = [g for g, v in o.group_votes(x.res["signals"]).items() if v == o.DOWN]
    first = x.core_sentence()
    sep = _t("list_sep", L)
    up_txt = _t("why_up", L, n=len(ups), names=sep.join(_w(_GROUP, g, L) for g in ups)) if ups else _t("why_no_up", L)
    down_txt = _t("why_down", L, n=len(downs), names=sep.join(_w(_GROUP, g, L) for g in downs)) if downs else _t("why_no_down", L)
    if x.d == o.DIR_HOLD:
        return _t("why_hold", L, first=first, reason=x.hold_reason(), tally=x.tally())
    gap = abs(x.t["up"] - x.t["down"])
    reach = _t("why_reach" if gap >= o.GROUP_MARGIN else "why_short", L)
    return _t("why_main", L, first=first, up=up_txt, down=down_txt, neutral=x.t["neutral"], gap=gap, margin=o.GROUP_MARGIN,
              reach=reach, tally=x.tally())


# ---------- 진입점 ----------

_NUM_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")
_AT_RE = re.compile(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}) KST")


def _numbers(s: str) -> set[str]:
    return {n.replace(",", "") for n in _NUM_RE.findall(s)}


def localize(answer: str, records: list[dict], locale: str) -> str | None:
    """전망 경로의 한국어 답(answer)을 그 언어 템플릿으로. 위 조건을 못 맞추면 None."""
    rec = next((r for r in records or [] if r.get("tool_name") == "stock_tool"), None)
    result = (rec or {}).get("result") or {}
    via = str(result.get("called_via") or "")
    if not via.startswith("forecast_"):
        return None
    mode = via.removeprefix("forecast_")
    if mode not in SUPPORTED_MODES or result.get("period") not in o.PERIODS:
        return None
    try:
        period = o.PERIODS[result["period"]]
        res = {k: result[k] for k in ("indicators", "signals", "label", "data")}
        company = SimpleNamespace(name=result["company_name"], ticker=result["ticker"], asset_type=None)
        m = _AT_RE.search(answer)
        now = (datetime.strptime(m.group(1), "%Y-%m-%d %H:%M").replace(tzinfo=KST) if m
               else (rec.get("called_at") or datetime.now(KST)))
        korean = (o.brief_answer(company, period, res, now) if mode == "brief"
                  else o.followup_answer(company, period, res, mode, now, {}))
        if korean != answer:
            return None
        out = (brief_answer(company, period, res, now, locale) if mode == "brief"
               else followup_answer(company, period, res, mode, now, locale))
    except (KeyError, TypeError, ValueError, IndexError, AttributeError):
        return None
    if out is None or not _numbers(out) <= _numbers(answer):
        return None
    return out
