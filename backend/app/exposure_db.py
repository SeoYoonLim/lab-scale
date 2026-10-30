"""포트폴리오 실질 노출 입력을 DB에서 읽는 얇은 계층(읽기 전용: 계좌를 만들지 않고 commit도 하지 않는다). 계산은 app/exposure.py가 한다.

쿼리 수는 보유 종목 수와 상관없이 고정이다(최대 3개):
  1) 보유 + 종목(company) 1
  2) 보유 종목별 최근 종가 2개(window row_number <= 2; 장중이면 오늘 행을 빼고도 직전 종가가 남게) 1
  3) 분해할 ETF들의 가장 최근 기준일 구성종목 + company 매핑(구성종목 코드 = company.ticker, LEFT JOIN) 1
보유가 없으면 1개, 보유 ETF가 없거나 모두 레버리지·인버스면 3)을 건너뛴다. 장 마감 전(평일 15:30 KST) 오늘 시세 행은
briefing과 같은 규칙(app.briefing.drop_open_session)으로 뺀다.
"""

from datetime import datetime

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.briefing import KST, drop_open_session
from app.data_status import is_stale
from app.etf import leverage_kind
from app.exposure import ConstituentIn, EtfIn, HoldingIn, compute_exposure
from app.models import Company, EtfHolding, Holding, StockPrice


def load_inputs(db: Session, owner_key: str, now: datetime) -> tuple[list[HoldingIn], dict[str, EtfIn]]:
    rows = db.execute(
        select(Company.id, Company.ticker, Company.name, Company.asset_type, Holding.quantity)
        .join(Company, Company.id == Holding.company_id)
        .where(Holding.device_id == owner_key)
        .order_by(Company.ticker)
    ).all()
    if not rows:
        return [], {}

    ranked = (
        select(
            StockPrice.company_id, StockPrice.price_date, StockPrice.close_price,
            func.row_number().over(partition_by=StockPrice.company_id, order_by=StockPrice.price_date.desc()).label("rn"),
        )
        .where(StockPrice.company_id.in_([r.id for r in rows]))
        .subquery()
    )
    price_rows = db.execute(select(ranked.c.company_id, ranked.c.price_date, ranked.c.close_price).where(ranked.c.rn <= 2)).all()
    latest: dict[int, tuple] = {}
    for r in drop_open_session(price_rows, now, day=lambda r: r.price_date):
        if r.close_price is None or float(r.close_price) <= 0:
            continue
        if r.company_id not in latest or r.price_date > latest[r.company_id][0]:
            latest[r.company_id] = (r.price_date, float(r.close_price))

    holdings = [
        HoldingIn(r.ticker, r.name, r.asset_type, int(r.quantity),
                  latest[r.id][1] if r.id in latest else None, latest[r.id][0] if r.id in latest else None)
        for r in rows
    ]

    # 레버리지·인버스 ETF는 분해하지 않으므로 구성종목을 읽지 않는다.
    etf_ids = {r.id: (r.ticker, r.name) for r in rows if r.asset_type == "etf" and not leverage_kind(r.name)}
    etfs: dict[str, EtfIn] = {}
    if etf_ids:
        newest = (
            select(EtfHolding.company_id, func.max(EtfHolding.as_of_date).label("d"))
            .where(EtfHolding.company_id.in_(list(etf_ids)))
            .group_by(EtfHolding.company_id)
            .subquery()
        )
        cons = db.execute(
            select(EtfHolding.company_id, EtfHolding.as_of_date, EtfHolding.holding_name, EtfHolding.holding_code,
                   EtfHolding.weight_pct, Company.ticker.label("linked_ticker"), Company.name.label("linked_name"))
            .join(newest, and_(newest.c.company_id == EtfHolding.company_id, newest.c.d == EtfHolding.as_of_date))
            .outerjoin(Company, Company.ticker == EtfHolding.holding_code)
        ).all()
        for r in cons:
            ticker, name = etf_ids[r.company_id]
            etf = etfs.get(ticker)
            if etf is None:
                etf = etfs[ticker] = EtfIn(ticker, name, r.as_of_date)
            etf.constituents.append(ConstituentIn(r.holding_name, r.holding_code, float(r.weight_pct), r.linked_ticker, r.linked_name))
    return holdings, etfs


def get_exposure(db: Session, owner_key: str, limit: int, now: datetime | None = None) -> dict:
    now = (now or datetime.now(KST)).astimezone(KST)
    holdings, etfs = load_inputs(db, owner_key, now)
    out = compute_exposure(holdings, etfs, now.date(), limit)
    vdate = out["valuation_date"]
    stale = bool(vdate) and is_stale(datetime.fromisoformat(vdate).date(), now.date())
    return {
        "generated_at": now.isoformat(),
        "stale": stale,
        "stale_notice": f"보유 종목의 최신 종가는 {vdate} 기준입니다." if stale else None,
        **out,
    }
