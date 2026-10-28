"""오늘의 개인화 브리핑(GET /api/briefing)의 계산부. DB, Ollama, 네트워크 없이 평범한 파이썬 값만 받아 dict를 돌려준다.

LLM을 쓰지 않는다. 응답의 모든 숫자(등락률, 거래량 배수, 평가금액, 비중, 손익)와 문장은 여기서 코드가 계산·조립한다.
문장은 관찰한 사실만 쓴다("거래량이 직전 20거래일 평균의 2.30배입니다"). 매수·매도를 권하는 표현은 쓰지 않는다
(tests/test_briefing.py가 모든 flag 문구를 evals.metrics.forbidden_hits로 검사한다).

DB 조회는 app/briefing_db.py가 하고, 이 모듈의 BriefingInputs로 넘겨준다.

# 정의 (등락률은 모두 저장된 종가에서 다시 계산한다. DB의 change_pct 컬럼은 쓰지 않는다)
- 1일 등락률 = 최신 종가 / 직전 거래일 종가 - 1 (%, 소수 둘째 자리 반올림)
- 5거래일 등락률 = 최신 종가 / 5거래일 전 종가 - 1. 5거래일 전 종가 = 최신 행에서 다섯 행 앞(구간 직전 종가, stock_tool/market_tool과 같은 정의)
- 거래량 배수 = 최신 거래량 / 직전 20거래일(최신 제외) 거래량 평균. 직전 20거래일이 다 있고 거래량이 비어 있지 않아야 하며,
  평균이 0이면 null이다(0으로 나누지 않는다)
- 비중 = 종목 평가금액 / 총자산(현금 + 가격이 있는 종목의 평가금액) (%). app/diagnosis.py와 같은 정의
- 평가금액은 DB의 최신 종가 x 수량이다(실시간 시세가 아니다)
- 장 마감(평일 15:30 KST) 전에는 오늘 날짜의 행을 쓰지 않는다. 장중에 수집한 행은 종가가 아니라 장중 값이고, 그 날의 거래량도
  반나절치라서 거래량 배수가 작게 나온다. 이때 브리핑은 직전 거래일 종가 기준이고 data_date가 그 날짜를 보여준다.
"""

import re
from dataclasses import dataclass, field, replace
from datetime import date, datetime, time, timedelta
from typing import NamedTuple
from zoneinfo import ZoneInfo

from app.data_status import is_stale
from app.disclaimer import DISCLAIMER
from app.etf import leverage_kind
from app.models.virtual_account import INITIAL_BALANCE

KST = ZoneInfo("Asia/Seoul")

# ---------- flag 임계값 (이상이면 flag. 표시되는 소수 둘째 자리 값으로 비교한다) ----------
DAY_MOVE_PCT = 3.0  # |1일 등락률| >= 3.0%
WEEK_MOVE_PCT = 7.0  # |5거래일 등락률| >= 7.0%
VOLUME_SPIKE_RATIO = 2.0  # 거래량 배수 >= 2.0
CONCENTRATION_PCT = 40.0  # 단일 종목 비중 >= 40.0% (app/diagnosis.py의 CONCENTRATION_TOP1_PCT와 같은 값)
NEW_DISCLOSURE_MIN = 1  # 최근 7일 공시 >= 1건

WEEK_TRADING_DAYS = 5
VOLUME_AVG_DAYS = 20
MARKET_CLOSE = time(15, 30)  # 이 시각(KST) 전의 오늘 행은 장중 값이라 제외한다
BARS_NEEDED = VOLUME_AVG_DAYS + 1  # 최신 1 + 직전 20거래일
BARS_FETCHED = BARS_NEEDED + 1  # DB 계층이 종목당 읽는 행 수. 장중이면 오늘 행을 버리므로 한 행 더 읽는다
RECENT_DAYS = 7  # 뉴스·공시 조회 기간(현재 시각 기준)
DISCLOSURE_SHOWN = 3  # 항목에 담는 공시 수(flag의 건수는 기간 내 전체)
MAX_HIGHLIGHTS = 5

FLAG_CONCENTRATION = "concentration"
FLAG_DAY_MOVE = "day_move"
FLAG_WEEK_MOVE = "week_move"
FLAG_VOLUME_SPIKE = "volume_spike"
FLAG_NEW_DISCLOSURE = "new_disclosure"
# highlights 정렬 순서(앞이 먼저). 규칙은 rank_highlights 참고.
FLAG_TYPES = (FLAG_CONCENTRATION, FLAG_DAY_MOVE, FLAG_WEEK_MOVE, FLAG_VOLUME_SPIKE, FLAG_NEW_DISCLOSURE)

SCOPE_HOLDING = "holding"
SCOPE_WATCHLIST = "watchlist"

EMPTY_MESSAGE = "관심종목을 추가하면 개인화된 브리핑을 볼 수 있습니다."
NO_HOLDINGS_MESSAGE = "보유 중인 종목이 없습니다. 모의투자에서 주문하면 보유 현황이 표시됩니다."
NO_ACCOUNT_MESSAGE = (
    f"모의투자 계좌가 아직 없습니다. 첫 주문 때 초기 자금 {INITIAL_BALANCE:,}원으로 계좌가 만들어집니다."
)
LEVERAGE_NOTICE = (
    "{kind} ETF입니다. 하루 단위 수익률을 정해진 배수(인버스는 반대 방향)로 따라가도록 설계돼 있어, "
    "여러 날 보유하면 기초지수 수익률에 배수를 곱한 값과 결과가 달라질 수 있습니다."
)


# ---------- 입력 값 ----------


class Bar(NamedTuple):
    """일별 시세 한 행. close/volume이 비어 있을 수 있다."""

    date: date
    close: float | None
    volume: int | None = None


class NewsRow(NamedTuple):
    title: str
    source: str | None
    url: str | None
    published_at: datetime | None


class DisclosureRow(NamedTuple):
    title: str
    disclosure_type: str | None
    source_url: str | None
    disclosed_at: datetime | None


@dataclass
class Instrument:
    ticker: str
    name: str
    asset_type: str = "stock"
    quantity: int | None = None  # 보유종목만
    avg_price: float | None = None  # 보유종목만


@dataclass
class BriefingInputs:
    """DB 계층이 채워서 build_briefing에 넘기는 값. 시세 행(bars)은 종목별로 순서 무관하게 넘겨도 된다."""

    has_account: bool = False
    cash_balance: float | None = None
    holdings: list[Instrument] = field(default_factory=list)
    watchlist: list[Instrument] = field(default_factory=list)  # 보유종목과 겹치는 것을 빼지 않아도 된다
    bars: dict[str, list[Bar]] = field(default_factory=dict)
    news: dict[str, list[NewsRow]] = field(default_factory=dict)
    disclosures: dict[str, list[DisclosureRow]] = field(default_factory=dict)
    kospi: list[tuple[date, float]] = field(default_factory=list)
    kosdaq: list[tuple[date, float]] = field(default_factory=list)
    usd_krw: list[tuple[date, float]] = field(default_factory=list)


# ---------- 시세 계산 ----------


def session_is_open(now: datetime) -> bool:
    """평일 15:30(KST) 전이면 True(공휴일은 모른다. 공휴일에는 오늘 행이 없으니 영향이 없다)."""
    now = _aware(now).astimezone(KST)
    return now.weekday() < 5 and now.time() < MARKET_CLOSE


def drop_open_session(rows: list, now: datetime, day=lambda r: r[0]) -> list:
    """장 마감 전이면 오늘 날짜의 행을 뺀다(장중 값은 종가가 아니다). day(row)는 행의 날짜를 꺼내는 함수."""
    if not session_is_open(now):
        return rows
    today = _aware(now).astimezone(KST).date()
    return [r for r in rows if day(r) != today]


def pct_change(new: float | None, old: float | None) -> float | None:
    """(new / old - 1) x 100을 소수 둘째 자리로. 값이 없거나 old가 0 이하이면 None."""
    if new is None or old is None or old <= 0:
        return None
    return round((new / old - 1) * 100, 2)


def price_stats(bars: list[Bar]) -> dict:
    """최신 종가, 1일·5거래일 등락률, 거래량과 직전 20거래일 평균 대비 배수. 모자란 값은 None.

    종가가 없거나 0 이하인 행은 없는 것으로 본다. 행이 하나도 없으면 data_available=False다.
    """
    rows = sorted((b for b in bars if b.close is not None and b.close > 0), key=lambda b: b.date, reverse=True)
    if not rows:
        return {
            "data_available": False,
            "latest_close": None,
            "latest_date": None,
            "prev_close": None,
            "day_change_pct": None,
            "week_change_pct": None,
            "volume": None,
            "avg_volume_20d": None,
            "volume_ratio": None,
        }
    latest = rows[0]
    prev = rows[1] if len(rows) > 1 else None
    base = rows[WEEK_TRADING_DAYS] if len(rows) > WEEK_TRADING_DAYS else None

    prior = rows[1 : 1 + VOLUME_AVG_DAYS]
    avg_volume = None
    if len(prior) == VOLUME_AVG_DAYS and all(b.volume is not None for b in prior):
        avg_volume = sum(b.volume for b in prior) / VOLUME_AVG_DAYS
    ratio = None
    if avg_volume and latest.volume is not None:  # 평균이 0이면 None
        ratio = round(latest.volume / avg_volume, 2)

    return {
        "data_available": True,
        "latest_close": latest.close,
        "latest_date": latest.date,
        "prev_close": prev.close if prev else None,
        "day_change_pct": pct_change(latest.close, prev.close) if prev else None,
        "week_change_pct": pct_change(latest.close, base.close) if base else None,
        "volume": latest.volume,
        "avg_volume_20d": round(avg_volume, 1) if avg_volume is not None else None,
        "volume_ratio": ratio,
    }


# ---------- 뉴스·공시 ----------


def _aware(ts: datetime) -> datetime:
    return ts if ts.tzinfo else ts.replace(tzinfo=KST)


def _iso_kst(ts: datetime) -> str:
    return _aware(ts).astimezone(KST).isoformat()


def _title_key(title: str) -> str:
    """제목 중복 판단용 키: 대소문자, 공백, 문장부호 차이를 없앤다."""
    return re.sub(r"[\W_]+", "", title or "").casefold()


def select_news(rows: list[NewsRow], limit: int, now: datetime) -> list[dict]:
    """최근 7일(now 기준) 뉴스를 최신순으로 limit건. 같은 기사(제목이 같거나 URL이 같음)는 가장 최신 하나만 남긴다."""
    cutoff = _aware(now) - timedelta(days=RECENT_DAYS)
    recent = [r for r in rows if r.published_at is not None and _aware(r.published_at) >= cutoff]
    recent.sort(key=lambda r: _aware(r.published_at), reverse=True)
    seen: set[str] = set()
    out: list[dict] = []
    for r in recent:
        keys = {f"t:{_title_key(r.title) or r.title}"}
        if r.url:
            keys.add(f"u:{r.url.strip()}")
        if keys & seen:
            continue
        seen |= keys
        out.append(
            {
                "title": r.title,
                "source": r.source,
                "published_at": _iso_kst(r.published_at),
                "url": r.url,
            }
        )
        if len(out) >= limit:
            break
    return out


def select_disclosures(rows: list[DisclosureRow], now: datetime) -> tuple[int, list[dict]]:
    """(최근 7일 공시 건수, 최신순 DISCLOSURE_SHOWN건)."""
    cutoff = _aware(now) - timedelta(days=RECENT_DAYS)
    recent = [r for r in rows if r.disclosed_at is not None and _aware(r.disclosed_at) >= cutoff]
    recent.sort(key=lambda r: _aware(r.disclosed_at), reverse=True)
    items = [
        {
            "title": r.title,
            "disclosure_type": r.disclosure_type,
            "disclosed_at": _iso_kst(r.disclosed_at),
            "source_url": r.source_url,
        }
        for r in recent[:DISCLOSURE_SHOWN]
    ]
    return len(recent), items


# ---------- flag ----------


def _flag(kind: str, scope: str, inst: Instrument, text: str, value: float, threshold: float, unit: str,
          on: date | None, direction: str | None = None) -> dict:
    return {
        "type": kind,
        "scope": scope,
        "ticker": inst.ticker,
        "name": inst.name,
        "text": text,
        "value": value,
        "threshold": threshold,
        "unit": unit,
        "date": on.isoformat() if on else None,
        "direction": direction,
    }


def _direction(value: float) -> str | None:
    return "up" if value > 0 else "down" if value < 0 else None


def make_flags(inst: Instrument, scope: str, stats: dict, disclosure_count: int, weight_pct: float | None,
               disclosure_date: date | None = None) -> list[dict]:
    """한 종목의 flag(관찰 사실 + 근거 수치). 순서는 FLAG_TYPES 순서다. disclosure_date는 가장 최근 공시일(new_disclosure의 date)."""
    flags: list[dict] = []
    on = stats["latest_date"]

    if weight_pct is not None and weight_pct >= CONCENTRATION_PCT:
        flags.append(_flag(
            FLAG_CONCENTRATION, scope, inst,
            f"{inst.name}의 비중이 총자산의 {weight_pct:.2f}%입니다(기준 {CONCENTRATION_PCT:g}% 이상).",
            weight_pct, CONCENTRATION_PCT, "%", on,
        ))

    day = stats["day_change_pct"]
    if day is not None and abs(day) >= DAY_MOVE_PCT:
        word = "상승" if day > 0 else "하락"
        flags.append(_flag(
            FLAG_DAY_MOVE, scope, inst,
            f"{inst.name}의 {on.isoformat()} 종가는 전 거래일 대비 {day:+.2f}% {word}했습니다.",
            day, DAY_MOVE_PCT, "%", on, _direction(day),
        ))

    week = stats["week_change_pct"]
    if week is not None and abs(week) >= WEEK_MOVE_PCT:
        word = "상승" if week > 0 else "하락"
        flags.append(_flag(
            FLAG_WEEK_MOVE, scope, inst,
            f"{inst.name}의 종가는 최근 {WEEK_TRADING_DAYS}거래일간 {week:+.2f}% {word}했습니다.",
            week, WEEK_MOVE_PCT, "%", on, _direction(week),
        ))

    ratio = stats["volume_ratio"]
    if ratio is not None and ratio >= VOLUME_SPIKE_RATIO:
        flags.append(_flag(
            FLAG_VOLUME_SPIKE, scope, inst,
            f"{inst.name}의 거래량이 직전 {VOLUME_AVG_DAYS}거래일 평균의 {ratio:.2f}배입니다.",
            ratio, VOLUME_SPIKE_RATIO, "배", on,
        ))

    if disclosure_count >= NEW_DISCLOSURE_MIN:
        flags.append(_flag(
            FLAG_NEW_DISCLOSURE, scope, inst,
            f"{inst.name}의 공시가 최근 {RECENT_DAYS}일 안에 {disclosure_count}건 있습니다.",
            float(disclosure_count), float(NEW_DISCLOSURE_MIN), "건", disclosure_date or on,
        ))
    return flags


def rank_highlights(flags: list[dict], limit: int = MAX_HIGHLIGHTS) -> list[dict]:
    """중요도 순 상위 limit개. 정렬 규칙(앞이 먼저):
    1. 보유종목(holding) flag가 관심종목(watchlist) flag보다 먼저
    2. flag 종류 순서: concentration > day_move > week_move > volume_spike > new_disclosure
    3. 기준 대비 크기 |value| / threshold가 큰 것
    4. 종목코드 오름차순(항상 같은 순서가 되게)
    """
    def key(f: dict):
        return (
            0 if f["scope"] == SCOPE_HOLDING else 1,
            FLAG_TYPES.index(f["type"]),
            -abs(f["value"]) / f["threshold"],
            f["ticker"],
        )

    return sorted(flags, key=key)[:limit]


# ---------- 포트폴리오 ----------


def evaluate_positions(cash: float, positions: list[tuple[Instrument, dict]]) -> dict:
    """보유종목 평가. positions는 (종목, price_stats 결과). 가격이 없는 종목은 평가금액/비중/손익이 None이고 합계에서 빠진다.

    반환: {"by_ticker": {ticker: {...}}, "holdings_value", "total_asset", "day_profit_loss", "unpriced": [이름...]}
    day_profit_loss = 가격이 2일치 이상 있는 보유종목의 수량 x (최신 종가 - 직전 종가) 합. 해당 종목이 없으면 None.
    """
    by_ticker: dict[str, dict] = {}
    holdings_value = 0.0
    unpriced: list[str] = []
    for inst, st in positions:
        close = st["latest_close"]
        if close is None:
            unpriced.append(inst.name)
            by_ticker[inst.ticker] = {
                "eval_amount": None, "profit_loss": None, "profit_loss_pct": None, "day_profit_loss": None,
                "_eval": None,
            }
            continue
        qty, avg = inst.quantity, inst.avg_price
        ev = close * qty
        holdings_value += ev
        prev = st["prev_close"]
        by_ticker[inst.ticker] = {
            "eval_amount": round(ev, 2),
            "profit_loss": round((close - avg) * qty, 2),
            "profit_loss_pct": pct_change(close, avg),
            "day_profit_loss": round((close - prev) * qty, 2) if prev is not None else None,
            "_eval": ev,
        }

    total_asset = cash + holdings_value
    for v in by_ticker.values():
        ev = v.pop("_eval")
        v["weight_pct"] = round(ev / total_asset * 100, 2) if ev is not None and total_asset > 0 else None

    day_parts = [v["day_profit_loss"] for v in by_ticker.values() if v["day_profit_loss"] is not None]
    return {
        "by_ticker": by_ticker,
        "holdings_value": round(holdings_value, 2),
        "total_asset": round(total_asset, 2),
        "day_profit_loss": round(sum(day_parts), 2) if day_parts else None,
        "unpriced": unpriced,
    }


# ---------- 시장 ----------


def quote(code: str, name: str, series: list[tuple[date, float]]) -> dict | None:
    """(날짜, 종가) 시계열의 최신값과 직전 값 대비 등락률. 데이터가 없으면 None. 직전 값이 없으면 change_pct만 None."""
    rows = sorted(((d, c) for d, c in series if c is not None and c > 0), reverse=True)
    if not rows:
        return None
    (d0, c0), prev = rows[0], (rows[1] if len(rows) > 1 else None)
    return {
        "code": code,
        "name": name,
        "date": d0.isoformat(),
        "close": c0,
        "change_pct": pct_change(c0, prev[1]) if prev else None,
    }


# ---------- 조립 ----------


def _item(inst: Instrument, scope: str, inputs: BriefingInputs, news_limit: int, now: datetime,
          weight_pct: float | None) -> tuple[dict, dict]:
    """(응답 항목, price_stats). 종목 하나의 데이터가 비어 있어도 이 종목 항목만 비고 다른 종목에는 영향이 없다."""
    st = price_stats(inputs.bars.get(inst.ticker, []))
    news = select_news(inputs.news.get(inst.ticker, []), news_limit, now)
    disc_count, disc_items = select_disclosures(inputs.disclosures.get(inst.ticker, []), now)
    kind = leverage_kind(inst.name) if inst.asset_type == "etf" else None
    item = {
        "ticker": inst.ticker,
        "name": inst.name,
        "asset_type": inst.asset_type,
        "data_available": st["data_available"],
        "latest_close": st["latest_close"],
        "latest_date": st["latest_date"].isoformat() if st["latest_date"] else None,
        "day_change_pct": st["day_change_pct"],
        "week_change_pct": st["week_change_pct"],
        "volume": st["volume"],
        "avg_volume_20d": st["avg_volume_20d"],
        "volume_ratio": st["volume_ratio"],
        "news": news,
        "disclosures": disc_items,
        "disclosure_count": disc_count,
        "notice": LEVERAGE_NOTICE.format(kind=kind) if kind else None,
        "flags": make_flags(inst, scope, st, disc_count, weight_pct,
                             date.fromisoformat(disc_items[0]["disclosed_at"][:10]) if disc_items else None),
    }
    return item, st


def build_briefing(inputs: BriefingInputs, news_limit: int, now: datetime) -> dict:
    now = _aware(now).astimezone(KST)
    inputs = replace(
        inputs,
        bars={k: drop_open_session(v, now, lambda b: b.date) for k, v in inputs.bars.items()},
        kospi=drop_open_session(inputs.kospi, now),
        kosdaq=drop_open_session(inputs.kosdaq, now),
        usd_krw=drop_open_session(inputs.usd_krw, now),
    )

    held = {i.ticker for i in inputs.holdings}
    watch = [i for i in inputs.watchlist if i.ticker not in held]  # 보유종목은 holdings에만 넣는다

    # 보유종목: 시세 -> 평가 -> 비중 -> flag 순서로 계산한다(flag가 비중을 쓴다).
    stats = {i.ticker: price_stats(inputs.bars.get(i.ticker, [])) for i in inputs.holdings}
    cash = inputs.cash_balance if inputs.has_account and inputs.cash_balance is not None else 0.0
    ev = evaluate_positions(cash, [(i, stats[i.ticker]) for i in inputs.holdings])

    holding_items: list[dict] = []
    for inst in inputs.holdings:
        pos = ev["by_ticker"][inst.ticker]
        item, _ = _item(inst, SCOPE_HOLDING, inputs, news_limit, now, pos["weight_pct"])
        item.update(
            quantity=inst.quantity,
            avg_price=inst.avg_price,
            eval_amount=pos["eval_amount"],
            weight_pct=pos["weight_pct"],
            profit_loss=pos["profit_loss"],
            profit_loss_pct=pos["profit_loss_pct"],
            day_profit_loss=pos["day_profit_loss"],
        )
        holding_items.append(item)
    # 비중이 큰 순(가격이 없는 종목은 뒤), 같으면 종목코드 순
    holding_items.sort(key=lambda it: (it["weight_pct"] is None, -(it["weight_pct"] or 0), it["ticker"]))

    watch_items = [_item(i, SCOPE_WATCHLIST, inputs, news_limit, now, None)[0] for i in watch]

    all_flags = [f for it in holding_items + watch_items for f in it["flags"]]

    # 데이터 기준일: 브리핑에 쓴 시세 중 가장 최근 날짜. 종목이 없으면 시장 지수 날짜.
    dates = [it["latest_date"] for it in holding_items + watch_items if it["latest_date"]]
    markets = {
        "kospi": quote("KS11", "코스피", inputs.kospi),
        "kosdaq": quote("KQ11", "코스닥", inputs.kosdaq),
        "usd_krw": quote("USD/KRW", "원/달러", inputs.usd_krw),
    }
    if not dates:
        dates = [m["date"] for k, m in markets.items() if m and k != "usd_krw"]
    data_date = max(dates) if dates else None
    stale = is_stale(date.fromisoformat(data_date) if data_date else None, now.date())
    stale_notice = None
    if stale:
        stale_notice = (
            f"마지막 데이터는 {data_date} 기준입니다." if data_date else "수집된 시세 데이터가 없습니다."
        )

    portfolio = _portfolio_summary(inputs, ev, holding_items)

    return {
        "generated_at": now.isoformat(),
        "data_date": data_date,
        "stale": stale,
        "stale_notice": stale_notice,
        "message": EMPTY_MESSAGE if not holding_items and not watch_items else None,
        "market": markets,
        "portfolio": portfolio,
        "holdings": holding_items,
        "watchlist": watch_items,
        "highlights": rank_highlights(all_flags),
        "disclaimer": DISCLAIMER,
    }


def _portfolio_summary(inputs: BriefingInputs, ev: dict, holding_items: list[dict]) -> dict:
    base = {"has_account": inputs.has_account, "initial_balance": INITIAL_BALANCE}
    if not inputs.has_account:
        return {
            **base, "cash_balance": None, "holdings_value": None, "total_asset": None, "profit_loss": None,
            "profit_loss_pct": None, "day_profit_loss": None, "top_weight": None, "weights": [],
            "message": NO_ACCOUNT_MESSAGE,
        }
    cash = inputs.cash_balance or 0.0
    total = ev["total_asset"]
    pnl = round(total - INITIAL_BALANCE, 2)
    weights = [
        {"ticker": it["ticker"], "name": it["name"], "weight_pct": it["weight_pct"]}
        for it in holding_items
        if it["weight_pct"] is not None
    ]
    message = None
    if not holding_items:
        message = NO_HOLDINGS_MESSAGE
    elif ev["unpriced"]:
        message = f"{', '.join(ev['unpriced'])}의 시세 데이터가 없어 평가금액과 비중 계산에서 제외했습니다."
    return {
        **base,
        "cash_balance": round(cash, 2),
        "holdings_value": ev["holdings_value"],
        "total_asset": total,
        "profit_loss": pnl,
        "profit_loss_pct": pct_change(total, INITIAL_BALANCE),
        "day_profit_loss": ev["day_profit_loss"],
        "top_weight": weights[0] if weights else None,
        "weights": weights,
        "message": message,
    }
