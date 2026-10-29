"""GET /api/status가 쓰는 데이터 기준일 집계. 읽기 전용 MAX 집계 위주로 쿼리 수를 작게 유지한다(요청당 7개)."""

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Company, DataRefreshLog, Disclosure, ExchangeRate, MarketIndex, News, StockPrice

KST = ZoneInfo("Asia/Seoul")

# 최근 거래일보다 이만큼(영업일) 이상 뒤처지면 stale. 주말은 세지 않는다(공휴일은 모르므로 평일 기준).
STALE_BUSINESS_DAYS = 2
# refresh_data.py의 단계 수. 한 번의 실행을 구분하려고 최근 행을 이만큼만 읽는다.
MAX_STEPS_PER_RUN = 6
# last_refresh가 보는 단계(scripts/refresh_data.STEP_ORDER와 같다, 테스트가 확인). 다른 스크립트가 남긴 행
# (예: scripts/backfill_history.py의 'backfill')은 "마지막 갱신 실행"으로 읽지 않는다.
REFRESH_STEPS = ("prices", "index", "fx", "news", "embed", "content")

NOTE = "주가는 종가 기준이며, 실시간 시세는 별도 엔드포인트로 제공됩니다."


def last_business_day(today: date) -> date:
    """today 이하의 가장 최근 평일(주말이면 직전 금요일)."""
    d = today
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def business_days_behind(latest: date, reference: date) -> int:
    """latest가 reference보다 몇 영업일(평일) 뒤처졌는지. latest >= reference이면 0."""
    if latest >= reference:
        return 0
    count, d = 0, latest
    while d < reference:
        d += timedelta(days=1)
        if d.weekday() < 5:
            count += 1
    return count


def is_stale(latest: date | None, today: date) -> bool:
    """latest(데이터 최신 날짜)가 최근 거래일(평일 기준) 대비 2영업일 이상 뒤처지면 True. 데이터가 없으면 True."""
    if latest is None:
        return True
    return business_days_behind(latest, last_business_day(today)) >= STALE_BUSINESS_DAYS


def _kst_date(ts: datetime | None) -> date | None:
    if ts is None:
        return None
    return (ts if ts.tzinfo else ts.replace(tzinfo=ZoneInfo("UTC"))).astimezone(KST).date()


def summarize_last_run(rows: list) -> dict | None:
    """최근 행(id 내림차순)에서 마지막 실행 한 번 분을 골라 요약한다. 기록이 없으면 None.

    같은 step이 다시 나오면 그 앞은 이전 실행이다.
    """
    run, seen = [], set()
    for r in rows:
        if r.step in seen:
            break
        seen.add(r.step)
        run.append(r)
    if not run:
        return None
    finished = [r.finished_at for r in run if r.finished_at is not None]
    failed = [r.step for r in reversed(run) if r.status == "failed"]
    return {
        "finished_at": max(finished).astimezone(KST).isoformat() if finished else None,
        "ok": not failed,
        "failed_steps": failed,
    }


def build_status(db: Session, now: datetime | None = None) -> dict:
    now = (now or datetime.now(KST)).astimezone(KST)
    today = now.date()

    by_type = dict(
        db.execute(
            select(Company.asset_type, func.max(StockPrice.price_date))
            .join(Company, Company.id == StockPrice.company_id)
            .group_by(Company.asset_type)
        ).all()
    )
    # 두 지수 중 더 뒤처진 쪽을 기준으로 삼는다(한쪽만 멈춰도 stale이 보이게).
    per_index = select(func.max(MarketIndex.price_date).label("d")).group_by(MarketIndex.index_code).subquery()
    index_latest = db.execute(select(func.min(per_index.c.d))).scalar()
    fx_latest = db.execute(select(func.max(ExchangeRate.price_date))).scalar()
    news_latest = _kst_date(db.execute(select(func.max(News.published_at))).scalar())
    disclosure_latest = _kst_date(db.execute(select(func.max(Disclosure.disclosed_at))).scalar())
    recent = db.execute(
        select(DataRefreshLog)
        .where(DataRefreshLog.step.in_(REFRESH_STEPS))
        .order_by(DataRefreshLog.id.desc())
        .limit(MAX_STEPS_PER_RUN)
    ).scalars().all()

    return {
        "generated_at": now.isoformat(),
        "sources": {
            "stock_price": {"latest_date": by_type.get("stock")},
            "etf_price": {"latest_date": by_type.get("etf")},
            "market_index": {"latest_date": index_latest, "stale": is_stale(index_latest, today)},
            "exchange_rate": {"latest_date": fx_latest},
            "news": {"latest_published_date": news_latest},
            "disclosure": {"latest_date": disclosure_latest},
        },
        "last_refresh": summarize_last_run(recent),
        "is_realtime": False,
        "note": NOTE,
    }
