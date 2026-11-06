"""GET /api/portfolio/exposure 문장의 언어별 템플릿(2026-11-05, en-US·ja-JP·zh-CN). 계산은 app.exposure 그대로이고,
ko-KR이 아닐 때만 결과의 문장 필드를 같은 값으로 다시 쓴다. 숫자 필드는 건드리지 않는다.

- flags: kind와 value_pct·threshold_pct, 그 기업의 effective_weight_pct·direct_weight_pct(companies 항목)로 다시 쓴다.
- data_warnings·stale_notice: 값 필드가 따로 없어서, app.exposure가 만든 한국어 문장을 정해진 패턴으로 읽어 값을 꺼낸다.
  패턴이 맞지 않으면 한국어 문장을 그대로 둔다(값을 추측하지 않는다).
- message, reason(reason_code), disclaimer(실질 노출 안내 + 서비스 면책)는 고정 문구다.
"""

import re

from app.exposure import CONCENTRATION_PCT, ETF_GAP_PCT, EXPOSURE_NOTE, MSG_NO_HOLDINGS, MSG_NO_PRICES, REASON_LEVERAGED, REASON_NO_DATA
from app.i18n import disclaimer

_CURRENCY = {"en-US": ("KRW ", ""), "ja-JP": ("", "ウォン"), "zh-CN": ("", "韩元")}
T = {
    "en-US": {
        MSG_NO_HOLDINGS: "You have no holdings. Buy stocks or ETFs in simulated trading to calculate effective exposure by company.",
        MSG_NO_PRICES: "Effective exposure can't be calculated because the latest closing prices of your holdings are unavailable.",
        "note": ("Effective exposure is a reference value calculated from your holdings' valuation (closing price basis) and ETF constituent weights, "
                 "to support investment decisions. Constituents and weights are as of the base date disclosed by the asset manager and may differ from today."),
        REASON_NO_DATA: "It was not broken down because there is no constituent data.",
        REASON_LEVERAGED: ("Leveraged and inverse ETFs were not broken down, because multiplying constituent weights as-is would misrepresent "
                           "the actual exposure given their multiples and futures/swap structures."),
        "concentration": "{name}'s effective weight is {v:.2f}% of your holdings' valuation (threshold {t:g}% or more).",
        "etf_inflates_weight": ("Because of exposure through ETFs, {name}'s effective weight ({eff:.2f}%) is {v:.2f}%p higher than "
                                "its direct weight ({direct:.2f}%) (threshold {t:g}%p or more)."),
        "no_price": "{name}({ticker}) has no latest closing price, so it was excluded from the effective exposure calculation.",
        "older_price": "{name}({ticker})'s latest closing price is as of {date} (other holdings are as of {other}).",
        "over": ("The sum of positive (+) constituent weights of {name} is {pct}%, which exceeds 100%. Allocation follows the disclosed weights as-is, "
                 "and 'Other' is negative by the excess ({amount}) (there may be negative-weight positions such as written options or swaps)."),
        "under": "The constituent weights of {name} sum to {pct}%. The remaining {rest}% ({amount}) is classified as 'Other (cash, derivatives, etc.)'.",
        "stale": "The constituent base date of {name} is {date}.",
        "stale_unknown": "The constituent base date of {name} is unknown.",
    },
    "ja-JP": {
        MSG_NO_HOLDINGS: "保有中の銘柄がありません。模擬投資で銘柄やETFを買うと、企業別の実質エクスポージャーを計算します。",
        MSG_NO_PRICES: "保有銘柄の最新終値を取得できないため、実質エクスポージャーを計算できません。",
        "note": ("実質エクスポージャーは保有評価額(終値基準)とETFの構成銘柄比率で計算した参考値で、投資判断の補助用です。"
                 "構成銘柄と比率は運用会社が開示した基準日の値のため、現在と異なる場合があります。"),
        REASON_NO_DATA: "構成銘柄データがないため分解しませんでした。",
        REASON_LEVERAGED: "レバレッジ・インバースETFは倍率や先物・スワップの構造のため、構成銘柄比率をそのまま掛けると実際のエクスポージャーを誤解させるため、分解しませんでした。",
        "concentration": "{name}の実質比率が保有評価額の{v:.2f}%です(基準{t:g}%以上)。",
        "etf_inflates_weight": "ETFを通じたエクスポージャーのため、{name}の実質比率({eff:.2f}%)が直接比率({direct:.2f}%)より{v:.2f}%p大きくなっています(基準{t:g}%p以上)。",
        "no_price": "{name}({ticker})の最新終値がないため、実質エクスポージャーの計算から除外しました。",
        "older_price": "{name}({ticker})の最新終値は{date}時点です(他の銘柄は{other})。",
        "over": "{name}の構成銘柄のプラス比率の合計が{pct}%で100%を超えています。配分は開示比率どおりに行い、超過分だけ「その他」がマイナス({amount})になります(売建オプション・スワップなどマイナス比率のポジションがある可能性があります)。",
        "under": "{name}の構成銘柄比率の合計は{pct}%です。残りの{rest}%({amount})は「その他(現金・デリバティブなど)」としました。",
        "stale": "{name}の構成銘柄の基準日は{date}です。",
        "stale_unknown": "{name}の構成銘柄の基準日が不明です。",
    },
    "zh-CN": {
        MSG_NO_HOLDINGS: "当前没有持仓。在模拟投资中买入股票或ETF后，将按企业计算实际敞口。",
        MSG_NO_PRICES: "无法获取持仓的最新收盘价，因此无法计算实际敞口。",
        "note": "实际敞口是根据持仓市值(按收盘价)和ETF成分股权重计算的参考值，仅用于辅助投资判断。成分股和权重为基金公司披露的基准日数据，可能与当前不同。",
        REASON_NO_DATA: "没有成分股数据，因此未进行分解。",
        REASON_LEVERAGED: "杠杆和反向ETF由于倍数以及期货、互换结构，直接乘以成分股权重会误导实际敞口，因此未进行分解。",
        "concentration": "{name}的实际权重为持仓市值的{v:.2f}%(基准{t:g}%以上)。",
        "etf_inflates_weight": "由于通过ETF产生的敞口，{name}的实际权重({eff:.2f}%)比直接权重({direct:.2f}%)高{v:.2f}%p(基准{t:g}%p以上)。",
        "no_price": "{name}({ticker})没有最新收盘价，已从实际敞口计算中排除。",
        "older_price": "{name}({ticker})的最新收盘价截至{date}(其他持仓截至{other})。",
        "over": "{name}的成分股正(+)权重合计为{pct}%，超过100%。按披露权重原样分配，超出部分使“其他”为负值({amount})(可能存在卖出期权、互换等负权重头寸)。",
        "under": "{name}的成分股权重合计为{pct}%。其余{rest}%({amount})归为“其他(现金、衍生品等)”。",
        "stale": "{name}的成分股基准日为{date}。",
        "stale_unknown": "{name}的成分股基准日未知。",
    },
}
_NO_PRICE_RE = re.compile(r"^(?P<name>.+)\((?P<ticker>[0-9A-Z]+)\)의 최신 종가가 없어 실질 노출 계산에서 뺐습니다\.$")
_OLDER_RE = re.compile(r"^(?P<name>.+)\((?P<ticker>[0-9A-Z]+)\)의 최신 종가는 (?P<date>\d{4}-\d{2}-\d{2}) 기준입니다\(다른 종목은 (?P<other>\d{4}-\d{2}-\d{2})\)\.$")
_OVER_RE = re.compile(r"^(?P<name>.+)의 구성종목 양\(\+\) 비중 합계가 (?P<pct>[\d.]+)%로 100%를 넘습니다\. .*'기타'가 음수\((?P<amount>-?[\d.,]+)원\)로 잡힙니다")
_UNDER_RE = re.compile(r"^(?P<name>.+)의 구성종목 비중 합계가 (?P<pct>[\d.]+)%입니다\. 나머지 (?P<rest>[\d.]+)%\((?P<amount>-?[\d.,]+)원\)는")
_STALE_RE = re.compile(r"^(?P<name>.+)의 구성종목 기준일은 (?P<date>\d{4}-\d{2}-\d{2})입니다\.$")
_STALE_UNKNOWN_RE = re.compile(r"^(?P<name>.+)의 구성종목 기준일을 알 수 없습니다\.$")


def _won(amount: str, locale: str) -> str:
    prefix, suffix = _CURRENCY[locale]
    return f"{prefix}{amount}{suffix}"


def _warning(w: dict, locale: str) -> dict:
    t, msg = T[locale], w.get("message") or ""
    if (m := _NO_PRICE_RE.match(msg)) and w.get("kind") == "no_price":
        return {**w, "message": t["no_price"].format(**m.groupdict())}
    if (m := _OLDER_RE.match(msg)) and w.get("kind") == "older_price":
        return {**w, "message": t["older_price"].format(**m.groupdict())}
    if (m := _OVER_RE.match(msg)) and w.get("kind") == "etf_weight_over_100":
        return {**w, "message": t["over"].format(name=m["name"], pct=m["pct"], amount=_won(m["amount"], locale))}
    if (m := _UNDER_RE.match(msg)) and w.get("kind") == "etf_weight_under_100":
        return {**w, "message": t["under"].format(name=m["name"], pct=m["pct"], rest=m["rest"], amount=_won(m["amount"], locale))}
    return w


def _stale(notice: str | None, locale: str) -> str | None:
    if not notice:
        return notice
    if m := _STALE_RE.match(notice):
        return T[locale]["stale"].format(**m.groupdict())
    if m := _STALE_UNKNOWN_RE.match(notice):
        return T[locale]["stale_unknown"].format(**m.groupdict())
    return notice


def _flag(f: dict, companies: dict, locale: str) -> dict:
    t = T[locale]
    if f.get("kind") == "concentration":
        return {**f, "message": t["concentration"].format(name=f["name"], v=f["value_pct"], t=f.get("threshold_pct", CONCENTRATION_PCT))}
    c = companies.get((f.get("code"), f.get("name")))
    if f.get("kind") == "etf_inflates_weight" and c is not None:
        return {**f, "message": t["etf_inflates_weight"].format(name=f["name"], eff=c["effective_weight_pct"],
                                                                direct=c["direct_weight_pct"], v=f["value_pct"],
                                                                t=f.get("threshold_pct", ETF_GAP_PCT))}
    return f


def localize(result: dict, locale: str) -> dict:
    """get_exposure 결과(dict)를 그 언어로. 숫자 필드·기업명·종목코드는 그대로다."""
    t = T[locale]
    companies = {(c.get("code"), c.get("name")): c for c in [*result.get("companies", []), *result.get("overlaps", [])]}
    return {
        **result,
        "message": t.get(result.get("message"), result.get("message")) if result.get("message") else result.get("message"),
        "flags": [_flag(f, companies, locale) for f in result.get("flags", [])],
        "data_warnings": [_warning(w, locale) for w in result.get("data_warnings", [])],
        "decomposed_etfs": [{**d, "stale_notice": _stale(d.get("stale_notice"), locale)} for d in result.get("decomposed_etfs", [])],
        "undecomposed_etfs": [{**u, "reason": t.get(u.get("reason_code"), u.get("reason"))} for u in result.get("undecomposed_etfs", [])],
        "disclaimer": f"{t['note']} {disclaimer(locale)}",
    }
