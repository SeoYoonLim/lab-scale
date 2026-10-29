"""ETF 구성종목 저장·조회(DB). 계산·검증·문구는 app/etf_holdings.py(순수)가 한다.

조회(holdings_view)는 읽기 전용이고 쿼리가 3개로 고정이다: 최신 기준일 1 + 그 날의 건수·합계·수집 시각 1 + 상위 N개 1.
저장(save_snapshot)은 (ETF, 기준일) 단위로 기존 행을 지우고 새로 넣는 한 트랜잭션이라, 같은 기준일을 다시 수집해도 결과가 같다(멱등).
"""

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.disclaimer import DISCLAIMER
from app.etf_holdings import (
    MSG_NO_DATA,
    MSG_NOT_COLLECTED,
    SOURCES,
    Snapshot,
    issuer_supported,
    stale_info,
    weight_warnings,
)
from app.models import Company, EtfHolding


def save_snapshot(db: Session, company_id: int, snap: Snapshot, collected_at: datetime | None = None) -> int:
    """(ETF, 기준일)의 구성종목을 통째로 교체한다. 저장한 행 수를 돌려준다. 호출한 쪽이 실패하면 rollback한다."""
    collected_at = collected_at or datetime.now(timezone.utc)
    db.execute(delete(EtfHolding).where(EtfHolding.company_id == company_id, EtfHolding.as_of_date == snap.as_of))
    db.add_all(
        EtfHolding(
            company_id=company_id, as_of_date=snap.as_of, holding_name=h.name[:200], holding_code=h.code[:30] if h.code else None,
            weight_pct=h.weight_pct, source=snap.source, collected_at=collected_at,
        )
        for h in snap.holdings
    )
    db.commit()
    return len(snap.holdings)


def holdings_view(db: Session, company: Company, limit: int, today: date | None = None) -> dict:
    """GET /api/etf/{ticker}/holdings의 본문(ETF 한 개). 데이터가 없으면 items=[]와 message다(에러 아님)."""
    today = today or datetime.now(ZoneInfo("Asia/Seoul")).date()
    base = {"ticker": company.ticker, "name": company.name, "asset_type": company.asset_type, "limit": limit,
            "disclaimer": DISCLAIMER}
    as_of = db.execute(select(func.max(EtfHolding.as_of_date)).where(EtfHolding.company_id == company.id)).scalar()
    if as_of is None:
        message = MSG_NOT_COLLECTED if issuer_supported(company.name) else MSG_NO_DATA
        return {**base, "as_of_date": None, "collected_at": None, "stale": False, "stale_notice": None, "source": None,
                "holding_count": 0, "total_weight_pct": None, "top_weight_pct": None, "items": [], "data_warnings": [],
                "message": message}

    agg = db.execute(
        select(func.count(), func.sum(EtfHolding.weight_pct), func.max(EtfHolding.collected_at), func.min(EtfHolding.source))
        .where(EtfHolding.company_id == company.id, EtfHolding.as_of_date == as_of)
    ).one()
    count, total, collected_at, source = agg[0], float(agg[1] or 0), agg[2], agg[3]
    rows = db.execute(
        select(EtfHolding.holding_name, EtfHolding.holding_code, EtfHolding.weight_pct)
        .where(EtfHolding.company_id == company.id, EtfHolding.as_of_date == as_of)
        .order_by(EtfHolding.weight_pct.desc(), EtfHolding.holding_name)
        .limit(limit)
    ).all()
    items = [{"rank": i, "name": r.holding_name, "code": r.holding_code, "weight_pct": float(r.weight_pct)} for i, r in enumerate(rows, 1)]
    stale, notice = stale_info(as_of, today)
    return {
        **base,
        "as_of_date": as_of.isoformat(),
        "collected_at": collected_at.isoformat() if collected_at else None,
        "stale": stale,
        "stale_notice": notice,
        "source": SOURCES.get(source, source),
        "holding_count": count,
        "total_weight_pct": round(total, 2),
        "top_weight_pct": round(sum(i["weight_pct"] for i in items), 2),
        "items": items,
        "data_warnings": weight_warnings(company.name, round(total, 2), count),
        "message": None,
    }


def chat_views(db: Session, companies: list[Company], today: date | None = None) -> tuple[list[dict], list[str]]:
    """채팅용: ETF들 중 데이터가 있는 것의 view 목록과 데이터가 없는 ETF 이름 목록."""
    views, missing = [], []
    for c in companies:
        v = holdings_view(db, c, 10, today)
        (views if v["items"] else missing).append(v if v["items"] else c.name)
    return views, missing
