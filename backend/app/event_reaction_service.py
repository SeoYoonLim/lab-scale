"""종목의 뉴스·공시 사건을 DB에서 읽어 app/event_reaction.py로 반응 지표를 계산한다(GET /api/stocks/{ticker}/event-reactions).

쿼리는 종목 시세 1번, 지수 1번, 공시 1번, 뉴스 1번이고 계산은 전부 메모리에서 한다.
"""

from datetime import date, datetime, time, timedelta
from typing import Literal

from sqlalchemy import select

from app.event_reaction import (
    DISCLAIMER,
    KST,
    Bar,
    compute_reaction,
    count_other_events,
    resolve_base_and_start,
    summarize,
    window_end_date,
)
from app.models import Company, Disclosure, MarketIndex, News, StockPrice
from app.tools.market_tool import index_code_for_market

EventType = Literal["disclosure", "news", "all"]


def _dedupe(events: list[dict]) -> list[dict]:
    """같은 날(KST) 같은 제목은 하나만 남긴다(입력은 최신순이므로 가장 최근 것이 남는다)."""
    seen, out = set(), []
    for e in events:
        key = (e["event_type"], e["day"], e["title"].strip())
        if key not in seen:
            seen.add(key)
            out.append(e)
    return out


def _load_events(db, company: Company, since: date) -> list[dict]:
    since_dt = datetime.combine(since, time.min, tzinfo=KST)
    events = []
    if company.asset_type != "etf":  # ETF는 공시 수집 대상이 아니다
        rows = db.execute(
            select(Disclosure.title, Disclosure.source_url, Disclosure.disclosed_at)
            .where(Disclosure.company_id == company.id, Disclosure.disclosed_at >= since_dt)
            .order_by(Disclosure.disclosed_at.desc())
        ).all()
        for title, url, at in rows:
            if at is None:
                continue
            d = at.astimezone(KST).date()  # 접수일만 있다(시각은 항상 00:00)
            events.append(
                {"event_type": "disclosure", "title": title, "url": url, "event_at": None, "event_date": d, "day": d}
            )
    rows = db.execute(
        select(News.title, News.url, News.published_at)
        .where(News.company_id == company.id, News.published_at >= since_dt)
        .order_by(News.published_at.desc())
    ).all()
    for title, url, at in rows:
        if at is None:
            continue
        at = at.astimezone(KST)
        day = at.date()
        # 자정(00:00:00) 그대로면 시각 없이 날짜만 들어온 것으로 보고 날짜만 아는 사건으로 다룬다.
        known = at.time() != time.min
        events.append(
            {
                "event_type": "news", "title": title, "url": url,
                "event_at": at if known else None, "event_date": None if known else day, "day": day,
            }
        )
    return _dedupe(events)


def build_event_reactions(
    db, company: Company, event_type: EventType = "all", limit: int = 10, since_days: int = 60,
    today: date | None = None,
) -> dict:
    today = today or datetime.now(KST).date()
    bars = [
        Bar(d, float(c), None if v is None else float(v))
        for d, c, v in db.execute(
            select(StockPrice.price_date, StockPrice.close_price, StockPrice.volume)
            .where(StockPrice.company_id == company.id)
            .order_by(StockPrice.price_date)
        ).all()
    ]
    dates = [b.date for b in bars]

    index_code = index_code_for_market(company.market) if company.asset_type != "etf" else None
    index_closes, missing_reason = None, None
    if company.asset_type == "etf":
        missing_reason = "ETF는 시장 지수 대비 초과수익률을 계산하지 않습니다"
    elif index_code is None:
        missing_reason = "상장 시장 구분이 없어 지수 비교를 못 합니다"
    else:
        index_closes = {
            d: float(c)
            for d, c in db.execute(
                select(MarketIndex.price_date, MarketIndex.close_price).where(MarketIndex.index_code == index_code)
            ).all()
        }
        if not index_closes:
            index_closes, missing_reason = None, "지수 데이터 없음"

    events = _load_events(db, company, today - timedelta(days=since_days))
    # 반응 시작일을 먼저 구해 두고, 다른 사건 수는 (유형 필터와 limit 이전의) 전체 사건 기준으로 센다.
    starts = [resolve_base_and_start(dates, e["event_at"], e["event_date"])[1] for e in events]

    chosen: list[tuple[int, dict]] = []
    for kind in ("disclosure", "news"):
        if event_type in (kind, "all"):
            chosen += [(i, e) for i, e in enumerate(events) if e["event_type"] == kind][:limit]
    chosen.sort(key=lambda p: (p[1]["day"], p[1]["event_at"] or datetime.min.replace(tzinfo=KST)), reverse=True)

    out = []
    for i, e in chosen:
        window = (starts[i], window_end_date(dates, starts[i]))
        r = compute_reaction(
            bars, event_at=e["event_at"], event_date=e["event_date"], index_closes=index_closes,
            index_missing_reason=missing_reason, other_events_in_window=count_other_events(i, starts, window),
        )
        out.append(
            {
                "event_type": e["event_type"], "title": e["title"], "url": e["url"],
                "event_at": e["event_at"].isoformat() if e["event_at"] else None,
                "event_date": e["day"].isoformat() if e["event_at"] is None else None,
                **r,
            }
        )
    return {
        "ticker": company.ticker, "company_name": company.name, "asset_type": company.asset_type,
        "price_as_of": dates[-1] if dates else None, "market_index": index_code,
        "events": out, "summary": summarize(out), "disclaimer": DISCLAIMER,
    }
