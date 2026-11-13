"""ETF 구성종목·비중 답의 언어별 템플릿(48차, 2026-11-13).

구성종목 답(app.etf_holdings.constituents_chat_answer)과 특정 종목 비중 답(holding_weight_answer)은 코드가 만든 한국어 문장이다.
예전에는 다른 언어로 보낼 때 LLM 보호 번역(app.i18n_translate)을 거쳤고, 비중·종목코드가 수십 개인 목록은 자리표시자 검증에 떨어져
데이터 요약(fallback_summary)으로 바뀌었다(48차 실측: "KODEX 200の構成銘柄を教えて" -> 구성종목 없는 시세 요약).
이제 같은 데이터(app.agent가 내부 meta["etf_holdings"]에 남긴 views/info)로 언어별 문장을 바로 만든다(outlook_i18n·quote_i18n과 같은 방식):
- 그 데이터로 다시 만든 한국어 답이 실제 답과 글자까지 같을 때만 쓴다. 다르면 None(기존 보호 번역).
- 숫자·날짜·종목코드·종목명·ETF 이름은 데이터 값 그대로다(표기도 한국어 답과 같다: 34.19%, 2026-10-30). 문장만 언어별이다.
- 공시 출처 이름은 아래 표로 옮긴다. 표에 없는 출처면 None(번역으로 넘긴다).
"""

from __future__ import annotations

from app import etf_holdings as E
from app.etf import constituents_answer, leverage_kind

LOCALES = ("en-US", "ja-JP", "zh-CN")

_SOURCE = {
    E.SOURCES[E.KODEX]: {"en-US": "the constituents file (PDF) on Samsung Asset Management's KODEX website",
                         "ja-JP": "サムスン資産運用 KODEX ウェブサイトの構成銘柄（PDF）",
                         "zh-CN": "三星资产运用 KODEX 官网的成分股（PDF）"},
    E.SOURCES[E.TIGER]: {"en-US": "the constituents file (PDF) on Mirae Asset Global Investments' TIGER ETF website",
                         "ja-JP": "未来アセット資産運用 TIGER ETF ウェブサイトの構成銘柄（PDF）",
                         "zh-CN": "未来资产运用 TIGER ETF 官网的成分股（PDF）"},
}
_HEAD = {"en-US": "Constituents and weights of {name}, as disclosed in {source} as of {as_of}.",
         "ja-JP": "{name}の構成銘柄と比率です。{source}で開示された{as_of}基準の値です。",
         "zh-CN": "{name}的成分股及权重。数据为{source}公布的{as_of}基准值。"}
_TOP = {"en-US": "Top constituents", "ja-JP": "上位構成銘柄", "zh-CN": "主要成分股"}
_LIMIT = {"en-US": "Only the top {n} are shown at a time.", "ja-JP": "一度に表示するのは上位{n}銘柄までです。",
          "zh-CN": "一次最多显示前{n}只。"}
_SUM = {"en-US": "The {k} holdings above total {top}%, and all {count} constituents excluding cash-like assets total {total}%.",
        "ja-JP": "上記{k}銘柄の比率合計は{top}%で、現金性資産を除く全{count}銘柄の比率合計は{total}%です。",
        "zh-CN": "以上{k}只的权重合计为{top}%，剔除现金类资产后全部{count}只成分股的权重合计为{total}%。"}
_LEV_SUM = {"en-US": "Leveraged and inverse ETFs hold derivatives such as futures, so total weights can differ from 100% "
                     "(for an inverse ETF, the futures weight is the size of the short position).",
            "ja-JP": "レバレッジ・インバースETFは先物などの派生商品を組み入れるため、比率合計が100%と異なる場合があります"
                     "（インバースETFの先物比率は売りポジションの大きさです）。",
            "zh-CN": "杠杆、反向ETF持有期货等衍生品，因此权重合计可能不等于100%（反向ETF的期货权重表示空头头寸的规模）。"}
_KIND = {"레버리지": {"en-US": "a leveraged", "ja-JP": "レバレッジ", "zh-CN": "杠杆"},
         "인버스": {"en-US": "an inverse", "ja-JP": "インバース", "zh-CN": "反向"},
         "인버스 레버리지": {"en-US": "a leveraged inverse", "ja-JP": "インバース・レバレッジ", "zh-CN": "反向杠杆"}}
_LEV_NOTICE = {
    "en-US": "※ Judging by its name, {name} is {kind} ETF. Leveraged and inverse ETFs are designed to track a set multiple (or the "
             "opposite direction) of the underlying index's daily return, so holding one for several days can, because of compounding, "
             "give a result different from the index's return over the same period multiplied by that multiple. The more the market "
             "swings up and down, the larger that gap can become.",
    "ja-JP": "※ 名称から見て、{name}は{kind}ETFです。レバレッジ・インバースETFは基礎指数の日次収益率を一定の倍率（インバースは逆方向）で"
             "追うよう設計されているため、複数日保有すると複利効果により、同じ期間の基礎指数の収益率に倍率を掛けた値と結果が異なることが"
             "あります。相場の上げ下げが繰り返されるほど、その差は大きくなり得ます。",
    "zh-CN": "※ 从名称来看，{name}是{kind}ETF。杠杆、反向ETF旨在按固定倍数（反向ETF为相反方向）跟踪基础指数的单日收益率，"
             "因此持有多日时，受复利效应影响，结果可能与同期基础指数收益率乘以该倍数的值不同。市场反复涨跌时，这一差距可能扩大。",
}
_STALE = {"en-US": "※ The latest constituent data collected is as of {as_of}. It may differ from the current holdings.",
          "ja-JP": "※ 最後に収集した構成銘柄の基準日は{as_of}です。最新の内容と異なる場合があります。",
          "zh-CN": "※ 最近一次收集的成分股基准日为{as_of}，可能与最新内容不同。"}
_WEIGHT_SUM = {"en-US": "※ The constituent weights total {total}%, {diff}%p away from the expected {expected}%. Cash-like assets "
                        "may have been excluded or some holdings may be missing.",
               "ja-JP": "※ 構成銘柄の比率合計は{total}%で、想定値（{expected}%）と{diff}%p異なります。現金性資産を除いた値であるか、"
                        "一部の銘柄が欠けている可能性があります。",
               "zh-CN": "※ 成分股权重合计为{total}%，与预期值（{expected}%）相差{diff}%p。可能是剔除了现金类资产，或缺少部分成分股。"}
_FOOTER = {"en-US": "※ Asset managers disclose constituents and weights every business day, and this service collects them manually, "
                    "so updates may lag. Please check the product page on the asset manager's website for the latest details. "
                    "This is for information only and is not an investment recommendation.",
           "ja-JP": "※ 構成銘柄と比率は運用会社が営業日ごとに開示しており、本サービスは手動で収集しているため反映が遅れることがあります。"
                    "最新の正確な内容は運用会社ウェブサイトの商品ページでご確認ください。この内容は情報提供を目的としたもので、投資勧誘ではありません。",
           "zh-CN": "※ 成分股及权重由基金管理公司每个工作日公布，本服务为手动收集，更新可能滞后。最新的准确内容请在基金管理公司官网的"
                    "产品页面确认。本内容仅供参考，不构成投资建议。"}
_OMITTED = {"en-US": "Constituents of {names} were omitted because up to {n} ETFs are shown at a time. Please ask about them separately.",
            "ja-JP": "{names}の構成銘柄は、一度に表示できるのが{n}本までのため省略しました。個別にご質問ください。",
            "zh-CN": "一次最多显示{n}只ETF，因此省略了{names}的成分股。请分别提问。"}
_REFUSAL = {"en-US": "This service does not provide constituent and weight data for {names} (the ETF data we have is price, volume "
                     "and related news). Constituents and weights change often, so please check the product page (constituents/PDF) "
                     "on the asset manager's official website or filings from the Korea Exchange and the Korea Financial Investment Association.",
            "ja-JP": "{names}の構成銘柄と組入比率のデータは本サービスでは提供していません（保有しているETFデータは価格・出来高と関連ニュースです）。"
                     "構成銘柄と比率は随時変わるため、正確な内容は運用会社公式サイトの商品ページ（構成銘柄/PDF明細）や"
                     "韓国取引所・金融投資協会の開示でご確認ください。",
            "zh-CN": "本服务不提供{names}的成分股及权重数据（现有的ETF数据为价格、成交量及相关新闻）。成分股和权重会随时变化，"
                     "准确内容请在基金管理公司官网的产品页面（成分股/PDF明细）或韩国交易所、韩国金融投资协会的公告中确认。"}
_W_FOUND = {"en-US": "{label} makes up {w}% of {etf}, ranking No. {rank} among all {count} constituents excluding cash-like assets, "
                     "as disclosed in {source} as of {as_of}.",
            "ja-JP": "{etf}における{label}の比率は{w}%です。現金性資産を除く全{count}銘柄中{rank}位で、{source}で開示された{as_of}基準の値です。",
            "zh-CN": "{label}在{etf}中的权重为{w}%，在剔除现金类资产后的全部{count}只成分股中排名第{rank}，数据为{source}公布的{as_of}基准值。"}
_W_MISSING = {"en-US": "{label} is not among the {count} constituents of {etf} disclosed in {source} as of {as_of}.",
              "ja-JP": "{source}で開示された{as_of}基準の{etf}の構成銘柄{count}銘柄に、{label}は含まれていません。",
              "zh-CN": "在{source}公布的{as_of}基准{etf}的{count}只成分股中，没有{label}。"}
_JOIN = {"en-US": ", ", "ja-JP": "、", "zh-CN": "、"}


class _Unsupported(Exception):
    """이 언어 템플릿으로 옮길 수 없는 내용(모르는 출처·경고 종류). 호출측은 None을 돌려준다."""


def _source(src: str | None, locale: str) -> str:
    if src not in _SOURCE:
        raise _Unsupported(src)
    return _SOURCE[src][locale]


def _notes(name: str, stale_notice: str | None, as_of: str | None, warnings: list[dict], locale: str) -> list[str]:
    out = []
    if stale_notice:
        if stale_notice != f"마지막으로 수집한 구성종목 기준일은 {as_of}입니다.":  # E.stale_info 문구와 다르면 옮기지 않는다
            raise _Unsupported("stale_notice")
        out.append(_STALE[locale].format(as_of=as_of))
    for w in warnings or []:
        if w.get("kind") != "weight_sum":
            raise _Unsupported(w.get("kind"))
        out.append(_weight_sum(name, w, locale))
    return out


def _weight_sum(name: str, w: dict, locale: str) -> str:
    """weight_warnings()의 문구에서 합계를 다시 읽어 같은 값으로 옮긴다(문구가 다르면 옮기지 않는다)."""
    import re

    m = re.match(r"구성종목 비중 합계가 (-?\d+\.\d{2})%로", w.get("message") or "")
    if not m:
        raise _Unsupported("weight_sum message")
    total = float(m.group(1))
    if E.weight_warnings(name, total, 1) != [w]:
        raise _Unsupported("weight_sum mismatch")
    expected = E.expected_total(name)
    return _WEIGHT_SUM[locale].format(total=f"{total:.2f}", expected=f"{expected:.0f}", diff=f"{abs(total - expected):.1f}")


def _leverage(name: str, locale: str, with_sum: bool) -> list[str]:
    kind = leverage_kind(name)
    if not kind:
        return []
    out = [_LEV_SUM[locale]] if with_sum else []
    return out + [_LEV_NOTICE[locale].format(name=name, kind=_KIND[kind][locale])]


def _holdings(view: dict, top_n: int | None, locale: str) -> str:
    name = view["name"]
    shown = min(top_n or E.TOP_N_CHAT, E.TOP_N_CHAT)
    items = view["items"][:shown]
    top_sum = round(sum(i["weight_pct"] for i in items), 2)
    parts = [
        _HEAD[locale].format(name=name, source=_source(view["source"], locale), as_of=view["as_of_date"]),
        _TOP[locale] + "\n" + "\n".join(E._line(i + 1, h) for i, h in enumerate(items)),
        _SUM[locale].format(k=len(items), top=f"{top_sum:.2f}", count=view["holding_count"], total=f"{view['total_weight_pct']:.2f}"),
    ]
    if top_n and top_n > E.TOP_N_CHAT:
        parts.insert(2, _LIMIT[locale].format(n=E.TOP_N_CHAT))
    parts += _leverage(name, locale, with_sum=True)
    parts += _notes(name, view.get("stale_notice"), view["as_of_date"], view.get("data_warnings"), locale)
    parts.append(_FOOTER[locale])
    return "\n\n".join(parts)


def _refusal(names: list[str], locale: str) -> str:
    parts = [_REFUSAL[locale].format(names=", ".join(names))]
    for n in names:
        parts += _leverage(n, locale, with_sum=False)
    return "\n\n".join(parts)


def _constituents(data: dict, locale: str) -> str:
    views, missing = data["views"], data.get("missing") or []
    blocks = [_holdings(v, data.get("top_n"), locale) for v in views[:E.MAX_CHAT_ETFS]]
    if len(views) > E.MAX_CHAT_ETFS:
        blocks.append(_OMITTED[locale].format(names=", ".join(v["name"] for v in views[E.MAX_CHAT_ETFS:]), n=E.MAX_CHAT_ETFS))
    if missing:
        blocks.append(_refusal(missing, locale))
    return "\n\n---\n\n".join(blocks)


def _weight(info: dict, locale: str) -> str:
    src = _source(info["source"], locale)
    lines = []
    for it in info["items"]:
        label = f"{it['name']}({it['ticker']})"
        fmt = dict(label=label, etf=info["etf"], count=info["holding_count"], source=src, as_of=info["as_of_date"])
        if it["found"]:
            lines.append(_W_FOUND[locale].format(w=f"{it['weight_pct']:.2f}", rank=it["rank"], **fmt))
        else:
            lines.append(_W_MISSING[locale].format(**fmt))
    parts = ["\n".join(lines)]
    parts += _leverage(info["etf"], locale, with_sum=False)
    parts += _notes(info["etf"], info.get("stale_notice"), info["as_of_date"], info.get("data_warnings"), locale)
    parts.append(_FOOTER[locale])
    return "\n\n".join(parts)


def korean(data: dict) -> str | None:
    """같은 데이터로 다시 만든 한국어 답(app.agent가 만든 답과 같은 함수·인자)."""
    kind = (data or {}).get("kind")
    if kind == "constituents":
        return E.constituents_chat_answer(data["views"], data.get("missing") or [], constituents_answer, data.get("top_n"))
    if kind == "weight":
        return E.holding_weight_answer(data["info"])
    return None


def localize(answer: str, data: dict | None, locale: str) -> str | None:
    """구성종목·비중 답을 그 언어 템플릿으로. 데이터로 다시 만든 한국어 답이 answer와 다르거나 옮길 수 없는 내용이면 None."""
    if locale not in LOCALES or not data:
        return None
    try:
        if korean(data) != answer:
            return None
        return _constituents(data, locale) if data["kind"] == "constituents" else _weight(data["info"], locale)
    except (_Unsupported, KeyError, TypeError, ValueError):
        return None
