"""성과·벤치마크 비교(app/performance_compare) 문장의 언어별 템플릿(ko-KR·en-US·ja-JP·zh-CN, 2026-11-06).

값 필드(kind·숫자·날짜·코드)로 문장만 만든다. 숫자·지수 코드·기준일·상태는 언어와 관계없이 같다.
"""

from __future__ import annotations

from app.i18n import disclaimer

_CUR = {"ko-KR": ("", "원"), "en-US": ("KRW ", ""), "ja-JP": ("", "ウォン"), "zh-CN": ("", "韩元")}
INDEX_NAME = {"KS11": {"ko-KR": "코스피", "en-US": "KOSPI", "ja-JP": "KOSPI", "zh-CN": "KOSPI"},
              "KQ11": {"ko-KR": "코스닥", "en-US": "KOSDAQ", "ja-JP": "KOSDAQ", "zh-CN": "KOSDAQ"}}

STATUS = {
    "ok": {"ko-KR": None, "en-US": None, "ja-JP": None, "zh-CN": None},
    "partial": {"ko-KR": "일부 날짜는 그날 종가가 없어 직전 종가로 평가했습니다(estimated 표시).",
                "en-US": "Some dates were valued with the previous close because that day's close was missing (marked estimated).",
                "ja-JP": "一部の日付は当日の終値がないため直前の終値で評価しました（estimatedで表示）。",
                "zh-CN": "部分日期因缺少当日收盘价，按前一收盘价估值（标记为estimated）。"},
    "no_account": {"ko-KR": "모의투자 계좌가 없습니다. 첫 주문 때 계좌가 만들어지고, 그 뒤부터 성과를 계산합니다.",
                   "en-US": "You don't have a simulated trading account yet. Performance is calculated after your first order creates one.",
                   "ja-JP": "模擬投資口座がありません。最初の注文で口座が作成され、その後から成果を計算します。",
                   "zh-CN": "尚无模拟投资账户。首次下单时将开设账户，之后开始计算业绩。"},
    "no_trades": {"ko-KR": "체결된 주문이 없어 성과를 계산할 수 없습니다. 주문이 체결되면 거래일 종가 기준으로 계산합니다.",
                  "en-US": "There are no executed orders, so performance can't be calculated. It is calculated from trading-day closes once orders are executed.",
                  "ja-JP": "約定した注文がないため成果を計算できません。注文が約定すると取引日の終値基準で計算します。",
                  "zh-CN": "没有已成交的订单，无法计算业绩。订单成交后将按交易日收盘价计算。"},
    "pending_only": {"ko-KR": "체결된 주문이 아직 종가에 반영되지 않았습니다. 다음 거래일 종가가 수집되면 계산합니다.",
                     "en-US": "Executed orders are not yet reflected in a close. They will be included once the next trading day's close is collected.",
                     "ja-JP": "約定した注文はまだ終値に反映されていません。次の取引日の終値が収集されると計算します。",
                     "zh-CN": "已成交订单尚未反映到收盘价。下一交易日收盘价采集后将进行计算。"},
    "no_prices": {"ko-KR": "수집된 시세가 없어 성과를 계산할 수 없습니다.", "en-US": "No prices have been collected, so performance can't be calculated.",
                  "ja-JP": "収集された株価がないため成果を計算できません。", "zh-CN": "尚未采集到行情，无法计算业绩。"},
    "history_unavailable": {"ko-KR": "체결 내역으로 현재 계좌를 다시 만들 수 없어(현금 또는 보유 수량 불일치) 과거 성과를 계산하지 않았습니다. 현재 보유를 과거에 소급해 수익률을 만들지 않습니다.",
                            "en-US": "Your current account can't be rebuilt from the trade history (cash or quantity mismatch), so past performance was not calculated. Current holdings are not applied to past dates to make up returns.",
                            "ja-JP": "約定履歴から現在の口座を再現できないため（現金または保有数量の不一致）、過去の成果は計算していません。現在の保有を過去に遡って収益率を作ることはしません。",
                            "zh-CN": "无法根据成交记录还原当前账户（现金或持仓数量不一致），因此未计算过去业绩。不会将当前持仓套用到过去日期来生成收益率。"},
}
BENCH = {
    "ok": {"ko-KR": None, "en-US": None, "ja-JP": None, "zh-CN": None},
    "partial": {"ko-KR": "일부 날짜의 지수가 없거나 포트폴리오 평가에 추정값이 있어 비교 결과는 참고용입니다.",
                "en-US": "Some dates lack index data or include estimated portfolio values, so the comparison is for reference only.",
                "ja-JP": "一部の日付で指数がないか、ポートフォリオ評価に推定値があるため、比較結果は参考用です。",
                "zh-CN": "部分日期缺少指数或组合估值含估计值，比较结果仅供参考。"},
    "no_data": {"ko-KR": "{index} 지수 데이터가 없어 비교하지 않았습니다.", "en-US": "No {index} index data, so no comparison was made.",
                "ja-JP": "{index}指数のデータがないため比較していません。", "zh-CN": "没有{index}指数数据，未进行比较。"},
    "insufficient_overlap": {"ko-KR": "포트폴리오와 {index} 지수의 공통 거래일이 2일 미만이라 비교하지 않았습니다.",
                             "en-US": "The portfolio and the {index} index share fewer than 2 trading days, so no comparison was made.",
                             "ja-JP": "ポートフォリオと{index}指数の共通取引日が2日未満のため比較していません。",
                             "zh-CN": "组合与{index}指数的共同交易日少于2天，未进行比较。"},
    "portfolio_unavailable": {"ko-KR": "포트폴리오 성과를 계산하지 못해 지수와 비교하지 않았습니다.",
                              "en-US": "Portfolio performance couldn't be calculated, so it was not compared with the index.",
                              "ja-JP": "ポートフォリオの成果を計算できなかったため、指数と比較していません。",
                              "zh-CN": "未能计算组合业绩，因此未与指数比较。"},
}
WARN = {
    "data_delayed": {"ko-KR": "마지막 종가는 {date}이며 기대 거래일({expected})보다 {lag}영업일 지연됐습니다. 오늘 시장 상황이 아닙니다.",
                     "en-US": "The latest close is {date}, {lag} business day(s) behind the expected trading day ({expected}). This is not today's market.",
                     "ja-JP": "最新の終値は{date}で、想定取引日（{expected}）より{lag}営業日遅れています。今日の市場状況ではありません。",
                     "zh-CN": "最新收盘价日期为{date}，比预期交易日（{expected}）滞后{lag}个工作日，并非今日市场情况。"},
    "history_mismatch": {"ko-KR": "체결 내역을 다시 계산한 현금({replayed})과 현재 현금({actual}) 또는 보유 수량이 맞지 않습니다.",
                         "en-US": "Cash rebuilt from trades ({replayed}) does not match current cash ({actual}) or holdings.",
                         "ja-JP": "約定履歴から再計算した現金（{replayed}）と現在の現金（{actual}）または保有数量が一致しません。",
                         "zh-CN": "根据成交记录重算的现金（{replayed}）与当前现金（{actual}）或持仓数量不一致。"},
    "pending_trades": {"ko-KR": "마지막 종가일({date}) 뒤에 체결된 주문 {count}건은 아직 반영하지 않았습니다.",
                       "en-US": "{count} order(s) executed after the last close date ({date}) are not yet included.",
                       "ja-JP": "最終終値日（{date}）以降に約定した注文{count}件はまだ反映していません。",
                       "zh-CN": "最后收盘日（{date}）之后成交的{count}笔订单尚未计入。"},
    "period_before_inception": {"ko-KR": "요청한 기간 시작({requested})이 첫 체결 전이라 {start}부터 계산했습니다.",
                                "en-US": "The requested start ({requested}) is before your first trade, so the calculation starts on {start}.",
                                "ja-JP": "要求した期間の開始（{requested}）が最初の約定より前のため、{start}から計算しました。",
                                "zh-CN": "请求的起始日（{requested}）早于首笔成交，因此从{start}开始计算。"},
    "estimated_prices": {"ko-KR": "{count}일({first}~{last})은 일부 종목의 그날 종가가 없어 직전 종가로 평가한 추정값입니다.",
                         "en-US": "{count} day(s) ({first}~{last}) are estimates valued with the previous close because some holdings had no close that day.",
                         "ja-JP": "{count}日（{first}~{last}）は一部銘柄の当日終値がないため、直前終値で評価した推定値です。",
                         "zh-CN": "{count}天（{first}~{last}）因部分持仓缺少当日收盘价，按前一收盘价估值，为估计值。"},
    "price_discontinuity": {"ko-KR": "{ticker}의 {date} 종가가 전일보다 {pct}% 변했습니다. 액면분할·병합 등 가격 불연속일 수 있어 수익률이 왜곡될 수 있습니다.",
                            "en-US": "{ticker}'s close on {date} changed {pct}% from the previous day. This may be a price discontinuity (split, consolidation, etc.) that distorts returns.",
                            "ja-JP": "{ticker}の{date}の終値が前日比{pct}%変化しました。株式分割・併合などの価格の不連続の可能性があり、収益率が歪む場合があります。",
                            "zh-CN": "{ticker}在{date}的收盘价较前一日变动{pct}%。可能存在拆股、合股等价格不连续，收益率可能失真。"},
    "benchmark_no_data": {"ko-KR": "{index} 지수 데이터가 없습니다.", "en-US": "No {index} index data.", "ja-JP": "{index}指数のデータがありません。",
                          "zh-CN": "没有{index}指数数据。"},
    "benchmark_insufficient_overlap": {"ko-KR": "{index} 지수와 공통 거래일이 {common_days}일뿐입니다.", "en-US": "Only {common_days} trading day(s) in common with {index}.",
                                       "ja-JP": "{index}指数との共通取引日が{common_days}日しかありません。", "zh-CN": "与{index}指数的共同交易日仅{common_days}天。"},
    "benchmark_missing_days": {"ko-KR": "{index} 지수가 없는 {count}일({first}~{last})은 비교에서 뺐습니다.",
                               "en-US": "{count} day(s) without {index} data ({first}~{last}) were excluded from the comparison.",
                               "ja-JP": "{index}指数がない{count}日（{first}~{last}）は比較から除外しました。",
                               "zh-CN": "缺少{index}指数的{count}天（{first}~{last}）已从比较中排除。"},
    "benchmark_start_aligned": {"ko-KR": "포트폴리오와 {index} 지수의 첫 공통 거래일({start})을 양쪽의 시작으로 맞췄습니다.",
                                "en-US": "Both series start on the first common trading day with {index} ({start}).",
                                "ja-JP": "ポートフォリオと{index}指数の最初の共通取引日（{start}）を両方の開始日にしました。",
                                "zh-CN": "以组合与{index}指数的首个共同交易日（{start}）作为双方起点。"},
}
NOTE = {"ko-KR": "거래일 종가 기준의 가격 수익률입니다. 배당·분배금은 포함하지 않고, 분할·병합 등 기업행사를 따로 보정하지 않습니다. 외부 입출금 기능이 없어 단순 수익률이며 시간가중수익률과 같습니다. 과거 성과는 미래 성과를 보장하지 않으며 매수·매도 권유가 아닙니다.",
        "en-US": "Price return based on trading-day closes. Dividends and distributions are not included, and corporate actions such as splits are not adjusted separately. There are no external deposits or withdrawals, so this is a simple return, equal to the time-weighted return. Past performance does not guarantee future results and is not a recommendation to buy or sell.",
        "ja-JP": "取引日の終値基準の価格収益率です。配当・分配金は含まず、株式分割・併合などのコーポレートアクションは別途補正していません。外部の入出金機能がないため単純収益率で、時間加重収益率と同じです。過去の成果は将来の成果を保証せず、売買の推奨ではありません。",
        "zh-CN": "按交易日收盘价计算的价格收益率。不含股息、分配金，也未单独调整拆股、合股等公司行为。由于没有外部出入金功能，为简单收益率，与时间加权收益率相同。过往业绩不代表未来表现，并非买卖建议。"}


def won(v, L):
    if v is None:
        return "-"
    pre, post = _CUR[L]
    return f"{pre}{v:,.0f}{post}" if float(v).is_integer() else f"{pre}{v:,.2f}{post}"


def warning_text(w: dict, L: str) -> str:
    idx = INDEX_NAME.get(w.get("code"), {}).get(L, w.get("code"))
    vals = {**w, "index": idx, "replayed": won(w.get("replayed_cash"), L), "actual": won(w.get("actual_cash"), L)}
    return WARN[w["kind"]][L].format(**vals)


def localize(result: dict, L: str) -> dict:
    out = {**result, "locale": L, "benchmark_name": INDEX_NAME[result["benchmark_code"]][L]}
    out["message"] = STATUS[result["status"]][L]
    out["warnings"] = [{**w, "message": warning_text(w, L)} for w in result["warnings"]]
    cmp = result["comparison"]
    msg = BENCH[cmp["status"]][L]
    out["comparison"] = {**cmp, "benchmark_name": INDEX_NAME[result["benchmark_code"]][L],
                         "message": msg.format(index=INDEX_NAME[result["benchmark_code"]][L]) if msg else None}
    out["disclaimer"] = f"{NOTE[L]} {disclaimer(L)}"
    return out
