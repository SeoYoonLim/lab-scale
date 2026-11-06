"""ETF 실질 노출(app/look_through) 문장의 언어별 템플릿(ko-KR·en-US·ja-JP·zh-CN, 2026-11-06).

계산 결과의 값 필드(kind와 숫자·날짜·이름)로 문장만 만든다. 숫자 필드는 건드리지 않으므로 모든 언어에서 금액·비중·종목코드·기준일·상태가 같다.
종목명·ETF 이름은 원문 그대로다.
"""

from __future__ import annotations

from app.i18n import disclaimer

LOCALES = ("ko-KR", "en-US", "ja-JP", "zh-CN")
_CUR = {"ko-KR": ("", "원"), "en-US": ("KRW ", ""), "ja-JP": ("", "ウォン"), "zh-CN": ("", "韩元")}

T: dict[str, dict[str, str]] = {
    # ---------- 데이터 경고 ----------
    "no_price": {"ko-KR": "{name}({ticker})의 최신 종가가 없어 평가 불가로 두었습니다(0원으로 계산하지 않음).",
                 "en-US": "{name}({ticker}) has no latest closing price, so it is marked as not valued (not counted as zero).",
                 "ja-JP": "{name}({ticker})は最新終値がないため評価不可としました（0ウォンとして計算していません）。",
                 "zh-CN": "{name}({ticker})没有最新收盘价，标记为无法估值（未按0计算）。"},
    "older_price": {"ko-KR": "{name}({ticker})의 최신 종가는 {date} 기준입니다(다른 종목은 {other}).",
                    "en-US": "{name}({ticker})'s latest close is as of {date} (other holdings: {other}).",
                    "ja-JP": "{name}({ticker})の最新終値は{date}時点です（他の銘柄は{other}）。",
                    "zh-CN": "{name}({ticker})的最新收盘价截至{date}（其他持仓截至{other}）。"},
    "etf_leveraged_inverse": {
        "ko-KR": "{kind} ETF {name}({ticker})의 구성종목은 분해하지 않았습니다. 배수·선물·스왑 구조 때문에 공시 구성 비중은 실제 경제적 노출과 다르고, 배수를 곱해 노출을 확정하지 않습니다.",
        "en-US": "{name}({ticker}) is {kind} ETF, so it was not broken down into constituents. Because of its multiple and futures/swap structure, the disclosed weights differ from its actual economic exposure, and no multiple is applied to fix an exposure.",
        "ja-JP": "{name}({ticker})は{kind}ETFのため構成銘柄に分解していません。倍率・先物・スワップの構造により開示された構成比率は実際の経済的エクスポージャーと異なり、倍率を掛けてエクスポージャーを確定することもしません。",
        "zh-CN": "{name}({ticker})是{kind}ETF，因此未分解为成分股。由于倍数及期货、互换结构，披露的成分权重与实际经济敞口不同，也不会乘以倍数来确定敞口。"},
    "etf_no_holdings_data": {"ko-KR": "{name}({ticker})의 구성종목 데이터가 없어 평가액 전체를 미확인 노출로 두었습니다.",
                             "en-US": "{name}({ticker}) has no constituent data, so its whole value is treated as unidentified exposure.",
                             "ja-JP": "{name}({ticker})は構成銘柄データがないため、評価額全体を未確認エクスポージャーとしました。",
                             "zh-CN": "{name}({ticker})没有成分股数据，其全部市值记为未确认敞口。"},
    "etf_invalid_weights": {"ko-KR": "{name}({ticker})의 구성 비중 데이터에 오류가 있어(한 종목 비중이 100% 초과 {n}건 또는 양수 비중 없음) 분해하지 않았습니다.",
                            "en-US": "{name}({ticker}) has invalid weight data ({n} constituent(s) above 100%, or no positive weights), so it was not broken down.",
                            "ja-JP": "{name}({ticker})の構成比率データに誤りがあるため（1銘柄の比率が100%超{n}件、または正の比率なし）分解していません。",
                            "zh-CN": "{name}({ticker})的成分权重数据有误（单只成分权重超过100%共{n}条，或没有正权重），因此未分解。"},
    "etf_weight_under_100": {"ko-KR": "{name}의 구성 비중 합계가 {pct}%입니다. 나머지 {rest}%({amount})는 기존 종목에 나누지 않고 미확인 노출로 두었습니다.",
                             "en-US": "{name}'s constituent weights sum to {pct}%. The remaining {rest}% ({amount}) is not spread over the listed constituents and is treated as unidentified exposure.",
                             "ja-JP": "{name}の構成比率の合計は{pct}%です。残りの{rest}%（{amount}）は既存銘柄に配分せず、未確認エクスポージャーとしました。",
                             "zh-CN": "{name}的成分权重合计为{pct}%。其余{rest}%（{amount}）未分配给已有成分股，记为未确认敞口。"},
    "etf_weight_over_100": {"ko-KR": "{name}의 양(+) 구성 비중 합계가 {pct}%로 100%를 넘습니다. 100%로 맞추지 않고 공시 비중 그대로 배분해 잔여가 음수({amount})입니다.",
                            "en-US": "{name}'s positive constituent weights sum to {pct}%, above 100%. Weights are used as disclosed without normalizing to 100%, so the residual is negative ({amount}).",
                            "ja-JP": "{name}のプラスの構成比率の合計が{pct}%で100%を超えています。100%に合わせず開示比率どおり配分したため、残余はマイナス（{amount}）です。",
                            "zh-CN": "{name}的正成分权重合计为{pct}%，超过100%。未调整为100%，按披露权重分配，因此剩余为负（{amount}）。"},
    "etf_negative_weights": {"ko-KR": "{name}에 음수 비중 구성종목이 {count}건(합계 {pct}%) 있습니다. 음수 비중은 기업 노출에 넣지 않았습니다.",
                             "en-US": "{name} has {count} constituent(s) with negative weights (total {pct}%). Negative weights are not counted as company exposure.",
                             "ja-JP": "{name}にマイナス比率の構成銘柄が{count}件（合計{pct}%）あります。マイナス比率は企業エクスポージャーに含めていません。",
                             "zh-CN": "{name}有{count}条负权重成分（合计{pct}%）。负权重未计入企业敞口。"},
    "etf_null_weights": {"ko-KR": "{name}에 비중이 비어 있는 구성종목이 {count}건 있어 계산에서 뺐습니다.",
                         "en-US": "{name} has {count} constituent(s) with missing weights, excluded from the calculation.",
                         "ja-JP": "{name}に比率が空の構成銘柄が{count}件あるため計算から除外しました。", "zh-CN": "{name}有{count}条成分缺少权重，已从计算中排除。"},
    "etf_duplicate_constituents": {"ko-KR": "{name}에 같은 종목이 {count}건 중복 저장돼 있습니다. 공시 행을 그대로 합산했습니다.",
                                   "en-US": "{name} has {count} duplicate constituent row(s). The disclosed rows were summed as-is.",
                                   "ja-JP": "{name}に同じ銘柄が{count}件重複して保存されています。開示行をそのまま合算しました。",
                                   "zh-CN": "{name}有{count}条重复保存的成分。已按披露行直接合计。"},
    "etf_nested_etf": {"ko-KR": "{name}의 구성종목 중 다른 ETF가 {count}개 있습니다. 중첩 ETF는 다시 분해하지 않아 그 금액 {amount}을 미확인 노출로 두었습니다.",
                       "en-US": "{name} holds {count} other ETF(s) as constituents. Nested ETFs are not broken down further, so {amount} is treated as unidentified exposure.",
                       "ja-JP": "{name}は他のETF{count}本を構成銘柄として保有しています。入れ子のETFは再分解しないため、{amount}を未確認エクスポージャーとしました。",
                       "zh-CN": "{name}持有{count}只其他ETF作为成分。嵌套ETF不再分解，因此{amount}记为未确认敞口。"},
    "etf_unlinked_constituents": {"ko-KR": "{name}의 구성종목 {count}건은 종목 목록과 연결되지 않아(해외 종목·채권·선물 등) 이름으로 묶었습니다.",
                                  "en-US": "{count} constituent(s) of {name} are not linked to our stock list (overseas stocks, bonds, futures, etc.) and are grouped by name.",
                                  "ja-JP": "{name}の構成銘柄{count}件は銘柄一覧と結び付かないため（海外銘柄・債券・先物など）名前でまとめました。",
                                  "zh-CN": "{name}有{count}条成分未与股票列表关联（海外股票、债券、期货等），已按名称归并。"},
    "etf_holdings_stale": {"ko-KR": "{name}의 구성종목 기준일은 {date}입니다. 현재 구성과 다를 수 있습니다.",
                           "en-US": "{name}'s constituents are as of {date} and may differ from the current composition.",
                           "ja-JP": "{name}の構成銘柄の基準日は{date}です。現在の構成と異なる場合があります。",
                           "zh-CN": "{name}的成分股基准日为{date}，可能与当前构成不同。"},
    # ---------- 플래그 ----------
    "concentration": {"ko-KR": "{name}의 실질 노출이 총자산의 {v}%입니다(기준 {t}% 이상, 직접 보유 {d}%).",
                      "en-US": "{name}'s look-through exposure is {v}% of total assets (threshold {t}% or more; direct holding {d}%).",
                      "ja-JP": "{name}の実質エクスポージャーは総資産の{v}%です（基準{t}%以上、直接保有{d}%）。",
                      "zh-CN": "{name}的实际敞口占总资产的{v}%（基准{t}%以上，直接持有{d}%）。"},
    "etf_inflates_weight": {"ko-KR": "{name}의 ETF를 통한 노출이 총자산의 {v}%라 실질 노출({total}%)이 직접 보유({d}%)보다 큽니다(기준 {t}%p 이상).",
                            "en-US": "{name}'s exposure through ETFs is {v}% of total assets, so its look-through exposure ({total}%) exceeds its direct holding ({d}%) (threshold {t}%p or more).",
                            "ja-JP": "{name}はETF経由のエクスポージャーが総資産の{v}%で、実質エクスポージャー（{total}%）が直接保有（{d}%）より大きいです（基準{t}%p以上）。",
                            "zh-CN": "{name}通过ETF的敞口占总资产的{v}%，因此实际敞口（{total}%）大于直接持有（{d}%）（基准{t}%p以上）。"},
    "single_etf_exposure": {"ko-KR": "{etf} 한 상품을 통한 {name} 노출이 총자산의 {v}%입니다(기준 {t}% 이상).",
                            "en-US": "Exposure to {name} through {etf} alone is {v}% of total assets (threshold {t}% or more).",
                            "ja-JP": "{etf}の1商品を通じた{name}へのエクスポージャーが総資産の{v}%です（基準{t}%以上）。",
                            "zh-CN": "仅通过{etf}对{name}的敞口占总资产的{v}%（基准{t}%以上）。"},
    # ---------- 상태·안내 ----------
    "msg_empty": {"ko-KR": "보유 중인 종목이 없습니다. 모의투자로 종목이나 ETF를 매수하면 실질 노출을 계산합니다.",
                  "en-US": "You have no holdings. Look-through exposure is calculated after you buy stocks or ETFs in simulated trading.",
                  "ja-JP": "保有中の銘柄がありません。模擬投資で銘柄やETFを買うと実質エクスポージャーを計算します。",
                  "zh-CN": "当前没有持仓。在模拟投资中买入股票或ETF后将计算实际敞口。"},
    "msg_no_prices": {"ko-KR": "보유 종목의 최신 종가가 없어 실질 노출을 계산할 수 없습니다.",
                      "en-US": "Look-through exposure can't be calculated because no latest closing prices are available for your holdings.",
                      "ja-JP": "保有銘柄の最新終値がないため、実質エクスポージャーを計算できません。", "zh-CN": "持仓没有最新收盘价，无法计算实际敞口。"},
    "price_stale": {"ko-KR": "평가 기준 종가는 {date}이며 기대 거래일({exp})보다 {lag}영업일 지연됐습니다.",
                    "en-US": "Valuation uses closing prices as of {date}, {lag} business day(s) behind the expected trading day ({exp}).",
                    "ja-JP": "評価に使った終値は{date}時点で、想定取引日（{exp}）より{lag}営業日遅れています。",
                    "zh-CN": "估值所用收盘价截至{date}，比预期交易日（{exp}）滞后{lag}个工作日。"},
    "sector_unavailable": {"ko-KR": "업종 분류 데이터가 없어 업종별 집중도는 계산하지 않았습니다.",
                           "en-US": "Sector concentration was not calculated because sector classification data is unavailable.",
                           "ja-JP": "業種分類データがないため、業種別の集中度は計算していません。", "zh-CN": "没有行业分类数据，未计算行业集中度。"},
    "sector_partial": {"ko-KR": "업종 정보가 있는 기업은 기업 노출 합계의 {pct}%뿐이라 업종별 집중도는 일부만 계산했습니다.",
                       "en-US": "Only {pct}% of company exposure has sector data, so sector concentration is partial.",
                       "ja-JP": "業種情報がある企業は企業エクスポージャー合計の{pct}%のみのため、業種別集中度は一部のみ計算しました。",
                       "zh-CN": "有行业信息的企业仅占企业敞口合计的{pct}%，因此行业集中度只计算了一部分。"},
    "sector_available": {"ko-KR": "업종별 집중도는 기업 노출 합계의 {pct}%를 덮습니다.", "en-US": "Sector concentration covers {pct}% of company exposure.",
                         "ja-JP": "業種別集中度は企業エクスポージャー合計の{pct}%をカバーします。", "zh-CN": "行业集中度覆盖企业敞口合计的{pct}%。"},
    "leverage_notice": {"ko-KR": "공시 구성 비중이며 경제적 노출이 아닙니다(배수를 곱하지 않음).",
                        "en-US": "Disclosed constituent weights, not economic exposure (no multiple applied).",
                        "ja-JP": "開示された構成比率であり、経済的エクスポージャーではありません（倍率は掛けていません）。",
                        "zh-CN": "此为披露的成分权重，并非经济敞口（未乘以倍数）。"},
    "note": {"ko-KR": "실질 노출은 보유 평가액과 운용사가 공시한 ETF 구성 비중(구성 기준일 값)으로 계산한 참고 값입니다. 비중의 분모는 총자산(현금 + 주식 + ETF)이고 ETF 구성종목 금액을 총자산에 다시 더하지 않습니다. 매수·매도 권유가 아닙니다.",
             "en-US": "Look-through exposure is a reference value calculated from your holdings' value and the ETF constituent weights disclosed by asset managers (as of their base date). Weights use total assets (cash + stocks + ETFs) as the denominator, and constituent amounts are not added to total assets again. This is not a recommendation to buy or sell.",
             "ja-JP": "実質エクスポージャーは保有評価額と運用会社が開示したETF構成比率（構成基準日の値）で計算した参考値です。比率の分母は総資産（現金＋株式＋ETF）で、ETF構成銘柄の金額を総資産に再び加えません。売買の推奨ではありません。",
             "zh-CN": "实际敞口是根据持仓市值和基金公司披露的ETF成分权重（成分基准日数据）计算的参考值。权重的分母为总资产（现金＋股票＋ETF），不会将ETF成分金额再次计入总资产。并非买卖建议。"},
}
KIND = {"레버리지": {"ko-KR": "레버리지", "en-US": "a leveraged", "ja-JP": "レバレッジ", "zh-CN": "杠杆"},
        "인버스": {"ko-KR": "인버스", "en-US": "an inverse", "ja-JP": "インバース", "zh-CN": "反向"},
        "인버스 레버리지": {"ko-KR": "인버스 레버리지", "en-US": "a leveraged inverse", "ja-JP": "インバース・レバレッジ", "zh-CN": "反向杠杆"}}


def won(v: float | None, L: str) -> str:
    if v is None:
        return "-"
    pre, post = _CUR[L]
    num = f"{v:,.0f}" if float(v).is_integer() else f"{v:,.2f}"
    return f"{pre}{num}{post}"


def _g(v) -> str:
    return f"{v:g}" if isinstance(v, float) else str(v)


def warning_text(w: dict, L: str) -> str:
    k = w["kind"]
    if k == "etf_leveraged_inverse":
        return T[k][L].format(name=w["name"], ticker=w["ticker"], kind=KIND.get(w.get("leverage_kind"), {}).get(L, w.get("leverage_kind") or ""))
    if k == "etf_invalid_weights":
        return T[k][L].format(name=w["name"], ticker=w["ticker"], n=w.get("over_100_count", 0))
    vals = {**w, "amount": won(w.get("amount"), L)}
    return T[k][L].format(**{x: (_g(y) if isinstance(y, float) and x != "amount" else y) for x, y in vals.items()})


def flag_text(f: dict, L: str) -> str:
    k = f["kind"]
    if k == "concentration":
        return T[k][L].format(name=f["name"], v=f["value_pct"], t=_g(f["threshold_pct"]), d=f.get("direct_weight_pct") or 0.0)
    if k == "etf_inflates_weight":
        return T[k][L].format(name=f["name"], v=f["value_pct"], total=f["total_weight_pct"], d=f.get("direct_weight_pct") or 0.0,
                              t=_g(f["threshold_pct"]))
    return T[k][L].format(name=f["name"], etf=f["etf_name"], v=f["value_pct"], t=_g(f["threshold_pct"]))


def localize(result: dict, L: str) -> dict:
    """계산 결과(dict)에 그 언어 문장을 붙인다(값 필드는 그대로). 같은 결과를 언어만 바꿔 여러 번 불러도 된다."""
    out = {**result, "locale": L}
    out["flags"] = [{**f, "message": flag_text(f, L)} for f in result["flags"]]
    out["data_warnings"] = [{**w, "message": warning_text(w, L)} for w in result["data_warnings"]]
    out["message"] = (T["msg_empty"][L] if result["status"] == "empty" else T["msg_no_prices"][L] if result["status"] == "no_prices" else None)
    lag = result.get("price_lag_business_days")
    out["stale"] = bool(lag)
    out["stale_notice"] = (T["price_stale"][L].format(date=result["valuation_date"], exp=result["expected_date"], lag=lag) if lag else None)
    sec = result["sector"]
    out["sector"] = {**sec, "message": T[f"sector_{sec['status']}"][L].format(pct=sec.get("covered_weight_pct"))}
    out["etfs"] = [{**e, "notice": T["leverage_notice"][L] if e.get("leverage_kind") else None} for e in result["etfs"]]
    out["disclaimer"] = f"{T['note'][L]} {disclaimer(L)}"
    return out
