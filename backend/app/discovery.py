"""종목 스크리닝(FR-13: AI 시장 관심 종목 탐색). 새 외부 데이터 소스 없이 이미 수집된 stock_price만 쓴다.

지원하는 분류(category):
- gainers: 최근 거래일 등락률 상위 N (급등주)
- losers: 최근 거래일 등락률 하위 N (급락주)
- volume_surge: 당일 거래량이 최근 window_days 거래일(당일 제외) 평균 거래량의 VOLUME_SURGE_MIN_RATIO배
  이상인 종목(거래량 급증), 배수(volume_ratio) 내림차순

market_tool/fx_tool과 같은 원칙으로 복잡한 예측/ML 없이 순수 집계·정렬만 한다(실용성 우선).

한계: "관심"의 기준이 등락률/거래량 수치뿐이다. 왜 움직였는지(뉴스·공시 같은 질적 신호)는 전혀 반영하지 않는다
(그건 news_tool/disclosure_tool/rag_search_tool의 몫이다). 당일 데이터가 없는 종목(상장폐지·수집 누락 등)은
집계에서 빠진다. 평균 거래량은 당일을 포함하지 않은 직전 window_days 거래일만 쓴다(당일 수치가 자기 평균을
끌어올려 배수가 희석되는 것을 막기 위함).
"""

from collections import defaultdict

from sqlalchemy import func

from app.models import Company, StockPrice
from app.tools.company_resolver import to_int

CATEGORIES = ("gainers", "losers", "volume_surge")
# "거래량 급증"으로 인정하는 최소 배수(당일 거래량 / 직전 window_days 평균 거래량).
VOLUME_SURGE_MIN_RATIO = 2.0


def rank_gainers(items: list[dict], limit: int) -> list[dict]:
    """등락률 상위 N(급등주). change_pct가 없는 종목은 제외."""
    ranked = [i for i in items if i["change_pct"] is not None]
    return sorted(ranked, key=lambda i: i["change_pct"], reverse=True)[:limit]


def rank_losers(items: list[dict], limit: int) -> list[dict]:
    """등락률 하위 N(급락주). change_pct가 없는 종목은 제외."""
    ranked = [i for i in items if i["change_pct"] is not None]
    return sorted(ranked, key=lambda i: i["change_pct"])[:limit]


def rank_volume_surge(items: list[dict], limit: int, min_ratio: float = VOLUME_SURGE_MIN_RATIO) -> list[dict]:
    """거래량 급증 N(volume_ratio가 min_ratio 이상인 종목만, 배수 내림차순)."""
    ranked = [i for i in items if i["volume_ratio"] is not None and i["volume_ratio"] >= min_ratio]
    return sorted(ranked, key=lambda i: i["volume_ratio"], reverse=True)[:limit]


_RANKERS = {"gainers": rank_gainers, "losers": rank_losers, "volume_surge": rank_volume_surge}


def get_trending(db, category: str, limit: int = 10, window_days: int = 20) -> dict:
    """category별 상위 N 종목을 돌려준다. stock_price에 데이터가 아직 없으면 found=False.

    category가 CATEGORIES에 없으면 예외 대신 found=False 응답을 돌려준다(LLM이 잘못된 값을 넣을 수 있어서,
    다른 tool들의 "종목을 찾지 못했습니다" 패턴과 같은 처리)."""
    if category not in CATEGORIES:
        return {
            "category": category,
            "found": False,
            "message": f"알 수 없는 category입니다. {', '.join(CATEGORIES)} 중 하나를 지정해주세요.",
        }

    limit = to_int(limit, default=10, hi=50)
    window_days = to_int(window_days, default=20, lo=5, hi=60)

    latest_date = db.query(func.max(StockPrice.price_date)).scalar()
    if latest_date is None:
        return {"category": category, "found": False, "message": "주가 데이터가 아직 수집되지 않았습니다."}

    latest_rows = (
        db.query(StockPrice, Company)
        .join(Company, Company.id == StockPrice.company_id)
        .filter(StockPrice.price_date == latest_date)
        .all()
    )

    # 평균 거래량은 당일을 뺀 직전 window_days 거래일만 쓴다(당일 수치가 자기 평균을 끌어올리는 것을 피하려고).
    prior_dates = [
        d
        for (d,) in db.query(StockPrice.price_date)
        .filter(StockPrice.price_date < latest_date)
        .distinct()
        .order_by(StockPrice.price_date.desc())
        .limit(window_days)
        .all()
    ]
    volumes_by_company: dict[int, list[int]] = defaultdict(list)
    if prior_dates:
        window_start = min(prior_dates)
        prior_rows = (
            db.query(StockPrice.company_id, StockPrice.volume)
            .filter(StockPrice.price_date >= window_start, StockPrice.price_date < latest_date)
            .all()
        )
        for company_id, volume in prior_rows:
            if volume is not None:
                volumes_by_company[company_id].append(volume)

    items = []
    for sp, company in latest_rows:
        vols = volumes_by_company.get(company.id, [])
        avg_volume = sum(vols) / len(vols) if vols else None
        volume_ratio = (
            round(sp.volume / avg_volume, 2)
            if (avg_volume and avg_volume > 0 and sp.volume is not None)
            else None
        )
        items.append(
            {
                "ticker": company.ticker,
                "company_name": company.name,
                "close_price": float(sp.close_price),
                "change_pct": float(sp.change_pct) if sp.change_pct is not None else None,
                "volume": sp.volume,
                "avg_volume": round(avg_volume, 2) if avg_volume is not None else None,
                "volume_ratio": volume_ratio,
            }
        )

    return {
        "category": category,
        "found": True,
        "price_date": latest_date.isoformat(),
        "window_days": len(prior_dates),
        "items": _RANKERS[category](items, limit),
    }
