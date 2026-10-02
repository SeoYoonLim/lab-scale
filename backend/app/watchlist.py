"""관심종목(watchlist). FR-12. 로그인이 없어 프론트의 디바이스ID(X-Device-Id)로 구분한다(app/api/deps.py).

db/schema.sql 원안에는 없던 확장이다(README "서윤님이 설계한 스키마와..." 참고).
"""

from sqlalchemy import func

from app.models import Company, StockPrice, Watchlist


def add_item(db, device_id: str, company_id: int) -> tuple[Watchlist, bool]:
    """(레코드, 이미 있었는지)를 돌려준다. 이미 있으면 새로 만들지 않고 기존 레코드를 그대로 돌려준다."""
    existing = (
        db.query(Watchlist)
        .filter(Watchlist.device_id == device_id, Watchlist.company_id == company_id)
        .first()
    )
    if existing is not None:
        return existing, True

    item = Watchlist(device_id=device_id, company_id=company_id)
    db.add(item)
    db.commit()
    db.refresh(item)
    return item, False


def remove_item(db, device_id: str, company_id: int) -> bool:
    """삭제했으면 True, 애초에 등록돼 있지 않았으면 False."""
    existing = (
        db.query(Watchlist)
        .filter(Watchlist.device_id == device_id, Watchlist.company_id == company_id)
        .first()
    )
    if existing is None:
        return False
    db.delete(existing)
    db.commit()
    return True


def _latest_prices(db, company_ids: list[int]) -> dict[int, StockPrice]:
    """company_id별 가장 최근 StockPrice row. 종목별 최근 날짜를 먼저 구한 뒤 그 (company_id, 날짜)로 다시 조인한다."""
    if not company_ids:
        return {}
    latest_dates = (
        db.query(StockPrice.company_id, func.max(StockPrice.price_date).label("max_date"))
        .filter(StockPrice.company_id.in_(company_ids))
        .group_by(StockPrice.company_id)
        .subquery()
    )
    rows = (
        db.query(StockPrice)
        .join(
            latest_dates,
            (StockPrice.company_id == latest_dates.c.company_id)
            & (StockPrice.price_date == latest_dates.c.max_date),
        )
        .all()
    )
    return {row.company_id: row for row in rows}


def list_items(db, device_id: str) -> list[dict]:
    """디바이스ID의 관심종목 전체(등록 최신순) + 종목별 최근 종가(실시간 아님, 프론트가 바로 쓸 수 있는 최소 정보)."""
    rows = (
        db.query(Watchlist, Company)
        .join(Company, Company.id == Watchlist.company_id)
        .filter(Watchlist.device_id == device_id)
        .order_by(Watchlist.created_at.desc())
        .all()
    )
    prices = _latest_prices(db, [c.id for _, c in rows])

    out = []
    for w, c in rows:
        p = prices.get(c.id)
        out.append(
            {
                "company_id": c.id,
                "ticker": c.ticker,
                "company_name": c.name,
                "added_at": w.created_at.isoformat(),
                "latest_close": float(p.close_price) if p else None,
                "latest_close_date": p.price_date.isoformat() if p else None,
                "change_pct": float(p.change_pct) if (p and p.change_pct is not None) else None,
            }
        )
    return out
