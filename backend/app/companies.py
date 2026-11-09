"""종목 목록/검색. 새 FR이 아니라, FR-02로 이미 수집된 company/stock_price 데이터를 프론트 "종목" 페이지가
직접 조회할 수 있게 하는 용도다(지금 그 화면은 mock 데이터로만 돌아간다).

app/watchlist.py의 최신 종가 조인 패턴을 그대로 쓴다.
"""

from sqlalchemy import func

from app.company_aliases import COMPANY_ALIASES
from app.models import Company, StockPrice
from app.tools.company_resolver import _key, rewrite_etf_brand

# q가 별칭 사전의 키와 일치하면 그 등록명도 매칭 대상에 넣는다('네이버' 검색 -> 'NAVER'도 찾게).
_ALIAS_BY_KEY = {_key(alias): name for alias, name in COMPANY_ALIASES.items()}


def _search_targets(q: str | None) -> set[str] | None:
    """필터링을 안 하면 None, 하면 매칭에 쓸 키 집합(검색어 자체 + 별칭의 등록명)을 돌려준다.

    ETF 브랜드의 한글 표기('코덱스200', '타이거 나스닥')는 영문('KODEX200', 'TIGER 나스닥')으로 바꾼 키도 함께 쓴다."""
    if not q or not q.strip():
        return None
    targets = set()
    for key in {_key(q), _key(rewrite_etf_brand(q))}:
        targets.add(key)
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


COMPANY_SORT_KEYS = {
    "id": lambda c: c.id,
    "name": lambda c: c.name,
    "ticker": lambda c: c.ticker,
}


def search_companies_page(
    db, q: str | None, limit: int, asset_type: str | None = None, offset: int = 0, sort: str = "id", order: str = "asc"
) -> tuple[int, list[dict]]:
    """종목명/티커 부분 일치 검색(대소문자/공백 무시, 별칭 반영) + 최근 종가·등락률. q가 없으면 전체.
    (조건에 맞는 전체 건수, offset부터 limit개)를 돌려준다. 건수는 limit/offset과 무관하다.

    asset_type('stock'|'etf')을 주면 그 유형만, 생략하면 전체(주식 + ETF)를 대상으로 한다.
    sort는 id(기본, 수집 순서) | name | ticker. 같은 값은 id가 방향을 따라 정한다."""
    query = db.query(Company)
    if asset_type is not None:
        query = query.filter(Company.asset_type == asset_type)
    companies = query.order_by(Company.id).all()

    targets = _search_targets(q)
    if targets is not None:
        companies = [c for c in companies if _matches(c, targets)]

    total = len(companies)
    key = COMPANY_SORT_KEYS[sort]
    companies = sorted(companies, key=lambda c: (key(c), c.id), reverse=order == "desc")[offset : offset + limit]
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
                "asset_type": c.asset_type,
                "latest_close": float(p.close_price) if p else None,
                "change_pct": float(p.change_pct) if (p and p.change_pct is not None) else None,
            }
        )
    return total, out


def search_companies(db, q: str | None, limit: int, asset_type: str | None = None) -> list[dict]:
    """search_companies_page의 첫 페이지(기본 정렬)만. 건수가 필요 없는 호출용."""
    return search_companies_page(db, q, limit, asset_type)[1]
