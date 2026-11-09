"""GET /api/briefing 문장의 언어별 템플릿(2026-11-05, en-US·ja-JP·zh-CN). 계산은 app.briefing 그대로이고, ko-KR이 아닐 때만
결과의 문장 필드를 같은 값(flag의 value·threshold·date·direction, 종목명)으로 다시 쓴다. 숫자 필드는 건드리지 않는다.

다시 쓰는 필드: flag.text(보유·관심·하이라이트), 종목 notice(레버리지·인버스), message, portfolio.message, stale_notice,
시장 지표 이름(market.*.name), disclaimer. 우리가 만든 한국어 문장 패턴과 맞지 않는 값은 그대로 둔다.
flag.unit(%·배·건)은 응답 스키마의 고정 코드값이라 바꾸지 않는다(화면 표시는 프런트 번역 리소스 common.unit_times·common.unit_count).
"""

import re

from app.briefing import EMPTY_MESSAGE, INITIAL_BALANCE, NO_ACCOUNT_MESSAGE, NO_HOLDINGS_MESSAGE, RECENT_DAYS, VOLUME_AVG_DAYS, WEEK_TRADING_DAYS
from app.etf import leverage_kind
from app.i18n import disclaimer

_KIND = {
    "레버리지": {"en-US": "a leveraged", "ja-JP": "レバレッジ", "zh-CN": "杠杆"},
    "인버스": {"en-US": "an inverse", "ja-JP": "インバース", "zh-CN": "反向"},
    "인버스 레버리지": {"en-US": "a leveraged inverse", "ja-JP": "インバース・レバレッジ", "zh-CN": "反向杠杆"},
}
_CURRENCY = {"en-US": ("KRW ", ""), "ja-JP": ("", "ウォン"), "zh-CN": ("", "韩元")}

T = {
    "en-US": {
        "concentration": "{name} makes up {v:.2f}% of total assets (threshold {t:g}% or more).",
        "day_move": "{name}'s closing price on {date} {verb} {v:+.2f}% from the previous trading day.",
        "week_move": "{name}'s closing price {verb} {v:+.2f}% over the last {n} trading days.",
        "volume_spike": "{name}'s trading volume is {v:.2f}x its average over the previous {n} trading days.",
        "new_disclosure": "{name} has {count} filing(s) in the last {n} days.",
        "up": "rose", "down": "fell",
        "notice": "This is {kind} ETF. It is designed to track a set multiple of the daily return (the opposite direction for inverse), "
                  "so holding it for several days can produce results different from the underlying index return times the multiple.",
        "empty": "Add stocks to your watchlist to see a personalized briefing.",
        "no_holdings": "You have no holdings. Your holdings will appear here after you place a simulated trade.",
        "no_account": "You don't have a simulated trading account yet. It will be created with initial funds of {amount} on your first order.",
        "unpriced": "Price data for {names} is unavailable, so it was excluded from valuation and weight calculations.",
        "stale": "The latest data is as of {date}.",
        "stale_none": "No price data has been collected.",
        "markets": {"코스피": "KOSPI", "코스닥": "KOSDAQ", "원/달러": "USD/KRW"},
    },
    "ja-JP": {
        "concentration": "{name}の比率が総資産の{v:.2f}%です(基準{t:g}%以上)。",
        "day_move": "{name}の{date}の終値は前取引日比{v:+.2f}%{verb}しました。",
        "week_move": "{name}の終値は直近{n}取引日で{v:+.2f}%{verb}しました。",
        "volume_spike": "{name}の出来高が直前{n}取引日平均の{v:.2f}倍です。",
        "new_disclosure": "{name}の開示が直近{n}日以内に{count}件あります。",
        "up": "上昇", "down": "下落",
        "notice": "{kind}ETFです。1日単位の収益率を決められた倍率(インバースは反対方向)で追うよう設計されているため、"
                  "複数日保有すると基礎指数の収益率に倍率を掛けた値と結果が異なる場合があります。",
        "empty": "ウォッチリストに銘柄を追加すると、パーソナライズされたブリーフィングを見られます。",
        "no_holdings": "保有中の銘柄がありません。模擬投資で注文すると保有状況が表示されます。",
        "no_account": "模擬投資口座がまだありません。最初の注文時に初期資金{amount}で口座が作成されます。",
        "unpriced": "{names}の相場データがないため、評価額と比率の計算から除外しました。",
        "stale": "最新データは{date}時点です。",
        "stale_none": "収集された相場データがありません。",
        "markets": {"코스피": "KOSPI", "코스닥": "KOSDAQ", "원/달러": "ウォン/ドル"},
    },
    "zh-CN": {
        "concentration": "{name}占总资产的{v:.2f}%(基准{t:g}%以上)。",
        "day_move": "{name}在{date}的收盘价较前一交易日{verb}{v:+.2f}%。",
        "week_move": "{name}的收盘价在最近{n}个交易日{verb}{v:+.2f}%。",
        "volume_spike": "{name}的成交量是前{n}个交易日平均值的{v:.2f}倍。",
        "new_disclosure": "{name}最近{n}天内有{count}条公告。",
        "up": "上涨", "down": "下跌",
        "notice": "这是{kind}ETF。它设计为按设定倍数跟踪单日收益率(反向ETF为反方向)，"
                  "持有多日时结果可能与基础指数收益率乘以倍数的值不同。",
        "empty": "将股票加入关注列表后，即可查看个性化简报。",
        "no_holdings": "当前没有持仓。在模拟投资中下单后将显示持仓情况。",
        "no_account": "尚未开设模拟投资账户。首次下单时将以初始资金{amount}开设账户。",
        "unpriced": "{names}没有行情数据，已从市值和权重计算中排除。",
        "stale": "最新数据截至{date}。",
        "stale_none": "尚未收集到行情数据。",
        "markets": {"코스피": "KOSPI", "코스닥": "KOSDAQ", "원/달러": "韩元/美元"},
    },
}
_UNPRICED_RE = re.compile(r"^(?P<names>.+)의 시세 데이터가 없어 평가금액과 비중 계산에서 제외했습니다\.$")
_STALE_RE = re.compile(r"^마지막 데이터는 (?P<date>\d{4}-\d{2}-\d{2}) 기준입니다\.$")
_SEP = {"en-US": ", ", "ja-JP": "、", "zh-CN": "、"}


def _won(x: float, locale: str) -> str:
    prefix, suffix = _CURRENCY[locale]
    return f"{prefix}{x:,}{suffix}"


def flag_text(f: dict, locale: str) -> str:
    t = T[locale]
    kind, v = f["type"], f["value"]
    if kind == "concentration":
        return t[kind].format(name=f["name"], v=v, t=f["threshold"])
    if kind == "day_move":
        return t[kind].format(name=f["name"], date=f["date"], v=v, verb=t["up" if v > 0 else "down"])
    if kind == "week_move":
        return t[kind].format(name=f["name"], n=WEEK_TRADING_DAYS, v=v, verb=t["up" if v > 0 else "down"])
    if kind == "volume_spike":
        return t[kind].format(name=f["name"], n=VOLUME_AVG_DAYS, v=v)
    if kind == "new_disclosure":
        return t[kind].format(name=f["name"], n=RECENT_DAYS, count=int(v))
    return f["text"]


def _flag(f: dict, locale: str) -> dict:
    return {**f, "text": flag_text(f, locale)}


def _item(item: dict, locale: str) -> dict:
    out = {**item, "flags": [_flag(f, locale) for f in item.get("flags") or []]}
    if item.get("notice"):
        kind = leverage_kind(item.get("name") or "")
        if kind:
            out["notice"] = T[locale]["notice"].format(kind=_KIND[kind][locale])
    return out


def _message(msg: str | None, locale: str) -> str | None:
    t = T[locale]
    if msg is None:
        return None
    if msg == EMPTY_MESSAGE:
        return t["empty"]
    if msg == NO_HOLDINGS_MESSAGE:
        return t["no_holdings"]
    if msg == NO_ACCOUNT_MESSAGE:
        return t["no_account"].format(amount=_won(INITIAL_BALANCE, locale))
    if m := _UNPRICED_RE.match(msg):
        return t["unpriced"].format(names=_SEP[locale].join(m.group("names").split(", ")))
    if m := _STALE_RE.match(msg):
        return t["stale"].format(date=m.group("date"))
    if msg == "수집된 시세 데이터가 없습니다.":
        return t["stale_none"]
    return msg


def localize(brief: dict, locale: str) -> dict:
    """get_briefing 결과(dict)를 그 언어로. 숫자·날짜·종목명·뉴스/공시 제목은 그대로다."""
    t = T[locale]
    market = {k: ({**m, "name": t["markets"].get(m.get("name"), m.get("name"))} if m else m)
              for k, m in (brief.get("market") or {}).items()}
    portfolio = dict(brief.get("portfolio") or {})
    if "message" in portfolio:
        portfolio["message"] = _message(portfolio["message"], locale)
    return {
        **brief,
        "stale_notice": _message(brief.get("stale_notice"), locale),
        "message": _message(brief.get("message"), locale),
        "market": market,
        "portfolio": portfolio,
        "holdings": [_item(i, locale) for i in brief.get("holdings") or []],
        "watchlist": [_item(i, locale) for i in brief.get("watchlist") or []],
        "highlights": [_flag(f, locale) for f in brief.get("highlights") or []],
        "disclaimer": disclaimer(locale),
    }
