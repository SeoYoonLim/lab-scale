"""종목 목록/검색. 새 FR이 아니라, FR-02로 이미 수집된 company/stock_price 데이터를 프론트 "종목" 페이지가
직접 조회할 수 있게 하는 용도다(지금 그 화면은 mock 데이터로만 돌아간다).

app/watchlist.py의 최신 종가 조인 패턴을 그대로 쓴다.
"""

from sqlalchemy import func

from app.company_aliases import COMPANY_ALIASES
from app.models import Company, StockPrice
from app.tools.company_resolver import _key

# q가 별칭 사전의 키와 일치하면 그 등록명도 매칭 대상에 넣는다('네이버' 검색 -> 'NAVER'도 찾게).
_ALIAS_BY_KEY = {_key(alias): name for alias, name in COMPANY_ALIASES.items()}


def _search_targets(q: str | None) -> set[str] | None:
    """필터링을 안 하면 None, 하면 매칭에 쓸 키 집합(검색어 자체 + 별칭의 등록명)을 돌려준다."""
    if not q or not q.strip():
        return None
    key = _key(q)
    targets = {key}
    alias_name = _ALIAS_BY_KEY.get(key)
    if alias_name is not None:
        targets.add(_key(alias_name))
    return targets


def _matches(company: Company, targets: set[str]) -> bool:
    return any(t in _key(company.name) or t in _key(company.ticker) for t in targets)


def _latest_prices(db, company_ids: list[int]) -> dict[int, StockPrice]:
    """app/watchlist.py의 동일 이름 함수와 같은 패턴: 종목별 최근 날짜를 먼저 구한 뒤 그 (company_id, 날짜)로 다시 조인한다."""
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


def search_companies(db, q: str | None, limit: int) -> list[dict]:
    """종목명/티커 부분 일치 검색(대소문자/공백 무시, 별칭 반영) + 최근 종가·등락률. q가 없으면 상위 limit개."""
    companies = db.query(Company).order_by(Company.id).all()

    targets = _search_targets(q)
    if targets is not None:
        companies = [c for c in companies if _matches(c, targets)]

    companies = companies[:limit]
    prices = _latest_prices(db, [c.id for c in companies])

    out = []
    for c in companies:
        p = prices.get(c.id)
        out.append(
            {
                "ticker": c.ticker,
                "name": c.name,
                "market": c.market,
                "sector": c.sector,
                "latest_close": float(p.close_price) if p else None,
                "change_pct": float(p.change_pct) if (p and p.change_pct is not None) else None,
            }
        )
    return out
