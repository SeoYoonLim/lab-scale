"""브리핑 입력을 DB에서 읽는 얇은 계층(읽기 전용: 계좌를 만들지 않고 commit도 하지 않는다). 계산은 app/briefing.py가 한다.

쿼리 수는 종목 수와 상관없이 고정이다: 계좌 1 + 보유 1 + 관심종목 1 + 시세 1 + 뉴스 1 + 공시 1 + 시장 지수 1 + 환율 1
(+ ETF 뉴스 관련성 판단용 ETF 이름 1, 뉴스를 읽을 ETF가 있을 때만). 종목 묶음은 company_id IN (...)과 윈도 함수
(종목별 최근 N행)로 한 번에 읽는다.

기존 함수를 쓰지 않은 이유(재사용 검토 결과):
- portfolio.get_portfolio: 계좌가 없으면 만들고(쓰기), 종목마다 실시간 시세를 외부에서 조회한다(N회 네트워크).
- watchlist.list_items: 최근 종가 한 행만 줘서 5거래일 등락률·거래량 평균을 못 구한다.
- stock_tool/news_tool/disclosure_tool/fx_tool: 종목 이름 해석과 SessionLocal() 생성을 호출마다 하고 종목 하나씩만 읽는다.
  그래서 쿼리가 종목 수에 비례하고 한 세션/트랜잭션으로 묶을 수 없다. 대신 같은 모델·상수(PAIR_CODE, MARKET_INDEXES,
  ETF_NEWS_OVERFETCH)와 ETF 뉴스 관련성 판단(etf.mentions_etf_name)을 그대로 쓴다.
"""

from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.briefing import (
    BARS_FETCHED,
    BriefingInputs,
    Bar,
    DisclosureRow,
    Instrument,
    KST,
    NewsRow,
    RECENT_DAYS,
    build_briefing,
)
from app.etf import mentions_etf_name
from app.models import Company, Disclosure, ExchangeRate, Holding, MarketIndex, News, StockPrice, VirtualAccount, Watchlist
from app.models.market_index import MARKET_INDEXES
from app.tools.fx_tool import PAIR_CODE
from app.tools.news_tool import ETF_NEWS_OVERFETCH

MAX_DISCLOSURES_PER_COMPANY = 50


def _top_n_per_company(model, columns: list, company_col, order_col, n: int, *where):
    """company_col별로 order_col 내림차순 상위 n행(윈도 함수). 쿼리 하나로 모든 종목을 읽는다."""
    rn = func.row_number().over(partition_by=company_col, order_by=order_col.desc()).label("rn")
    sub = select(*columns, rn).where(*where).subquery()
    return select(*[sub.c[c.key] for c in columns]).where(sub.c.rn <= n).order_by(sub.c[company_col.key], sub.c.rn)


def load_inputs(db: Session, owner_key: str, news_limit: int, now: datetime) -> BriefingInputs:
    inputs = BriefingInputs()

    account = db.get(VirtualAccount, owner_key)
    if account is not None:
        inputs.has_account = True
        inputs.cash_balance = float(account.cash_balance)

    held_rows = db.execute(
        select(Holding.quantity, Holding.avg_price, Company.id, Company.ticker, Company.name, Company.asset_type)
        .join(Company, Company.id == Holding.company_id)
        .where(Holding.device_id == owner_key)
        .order_by(Holding.id)
    ).all()
    watch_rows = db.execute(
        select(Company.id, Company.ticker, Company.name, Company.asset_type)
        .select_from(Watchlist)
        .join(Company, Company.id == Watchlist.company_id)
        .where(Watchlist.device_id == owner_key)
        .order_by(Watchlist.created_at.desc(), Watchlist.id.desc())
    ).all()
    inputs.holdings = [Instrument(r.ticker, r.name, r.asset_type, int(r.quantity), float(r.avg_price)) for r in held_rows]
    inputs.watchlist = [Instrument(r.ticker, r.name, r.asset_type) for r in watch_rows]

    companies = {r.id: r for r in [*held_rows, *watch_rows]}  # id -> 행(보유/관심 중복은 한 번만)
    if companies:
        _load_company_data(db, inputs, companies, news_limit, now)

    _load_market(db, inputs)
    return inputs


def _load_company_data(db: Session, inputs: BriefingInputs, companies: dict, news_limit: int, now: datetime) -> None:
    ids = list(companies)
    ticker_of = {cid: r.ticker for cid, r in companies.items()}

    price_q = _top_n_per_company(
        StockPrice,
        [StockPrice.company_id, StockPrice.price_date, StockPrice.close_price, StockPrice.volume],
        StockPrice.company_id, StockPrice.price_date, BARS_FETCHED,
        StockPrice.company_id.in_(ids),
    )
    for r in db.execute(price_q):
        inputs.bars.setdefault(ticker_of[r.company_id], []).append(
            Bar(r.price_date, float(r.close_price) if r.close_price is not None else None, r.volume)
        )

    cutoff = now - timedelta(days=RECENT_DAYS)
    news_q = _top_n_per_company(
        News,
        [News.company_id, News.title, News.content, News.source, News.url, News.published_at],
        News.company_id, News.published_at, news_limit * ETF_NEWS_OVERFETCH,
        News.company_id.in_(ids), News.published_at >= cutoff,
    )
    news_rows = db.execute(news_q).all()
    etf_names = None
    for r in news_rows:
        if companies[r.company_id].asset_type == "etf":
            # news_tool과 같은 기준: 제목·본문에 이 ETF의 이름이 직접 나오는 기사만 쓴다.
            if etf_names is None:
                etf_names = [n for (n,) in db.execute(select(Company.name).where(Company.asset_type == "etf"))]
            others = [n for n in etf_names if n != companies[r.company_id].name]
            if not mentions_etf_name(f"{r.title} {r.content or ''}", companies[r.company_id].name, others):
                continue
        inputs.news.setdefault(ticker_of[r.company_id], []).append(NewsRow(r.title, r.source, r.url, r.published_at))

    stock_ids = [cid for cid, r in companies.items() if r.asset_type != "etf"]  # ETF는 공시를 수집하지 않는다
    if stock_ids:
        disc_q = _top_n_per_company(
            Disclosure,
            [Disclosure.company_id, Disclosure.title, Disclosure.disclosure_type, Disclosure.source_url, Disclosure.disclosed_at],
            Disclosure.company_id, Disclosure.disclosed_at, MAX_DISCLOSURES_PER_COMPANY,
            Disclosure.company_id.in_(stock_ids), Disclosure.disclosed_at >= cutoff,
        )
        for r in db.execute(disc_q):
            inputs.disclosures.setdefault(ticker_of[r.company_id], []).append(
                DisclosureRow(r.title, r.disclosure_type, r.source_url, r.disclosed_at)
            )


def _load_market(db: Session, inputs: BriefingInputs) -> None:
    """지수(코스피/코스닥)와 원/달러의 최근 2행(최신값과 전일 대비를 구하는 데 충분하다)."""
    idx_q = _top_n_per_company(
        MarketIndex,
        [MarketIndex.index_code, MarketIndex.price_date, MarketIndex.close_price],
        MarketIndex.index_code, MarketIndex.price_date, 2,
        MarketIndex.index_code.in_(list(MARKET_INDEXES)),
    )
    series: dict[str, list] = {}
    for r in db.execute(idx_q):
        series.setdefault(r.index_code, []).append((r.price_date, float(r.close_price)))
    inputs.kospi = series.get("KS11", [])
    inputs.kosdaq = series.get("KQ11", [])

    fx_q = (
        select(ExchangeRate.price_date, ExchangeRate.close_price)
        .where(ExchangeRate.pair_code == PAIR_CODE)
        .order_by(ExchangeRate.price_date.desc())
        .limit(2)
    )
    inputs.usd_krw = [(r.price_date, float(r.close_price)) for r in db.execute(fx_q)]


def get_briefing(db: Session, owner_key: str, news_limit: int, now: datetime | None = None) -> dict:
    now = now or datetime.now(KST)
    return build_briefing(load_inputs(db, owner_key, news_limit, now), news_limit, now)
