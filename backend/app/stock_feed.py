"""종목별 뉴스·공시 목록 GET /api/stocks/{ticker}/news, /disclosures(2026-11-10, 41차)의 조회와 응답 조립.

지금까지 종목 상세 화면은 뉴스·공시를 사건 반응 카드(event-reactions, 최근 60일·유형별 최대 30건)와 일일 브리핑으로만 볼 수 있었다.
이 모듈은 저장된 행을 그대로 페이지로 나눠 준다. 새로 계산하거나 요약하지 않는다.

뉴스
- 이 종목에 연결된 news 행(company_id) 중 is_relevant가 False가 아닌 것. 다른 경로(브리핑·사건 반응·news_tool·RAG)와 같은 필터다.
- 같은 URL이 여러 종목에 연결돼 있는 것은 정상이다(dev DB 2,518개 URL). 이 종목의 행만 보여주고 다른 종목 행은 지우거나 숨기지 않는다.
  같은 기사에 연결된 다른 종목은 related_tickers로 알려준다(페이지의 URL만 쿼리 1회).
- summary는 수집 때 네이버 뉴스 검색 API가 준 description(기사 앞부분 발췌)이다. 우리가 만든 요약이 아니다.
- published_at은 기사 게시 시각, collected_at은 이 행을 처음 저장한 시각(둘 다 ISO 8601 KST).

공시
- DART 접수일만 저장돼 있어(disclosed_at은 항상 KST 00:00) 시각 없이 날짜(disclosed_date)로 준다.
- 정정 공시([기재정정] 등)는 원본과 접수번호가 다른 별개 공시다(app.ingest_validation.is_correction). 중복으로 보지 않고 is_correction으로 표시한다.
- ETF는 공시 수집 대상이 아니다: 404가 아니라 200 + supported=false + message.

날짜 필터(date_from·date_to)는 KST 달력 날짜, 양끝 포함이다. 거꾸로 된 범위는 422(기록 API와 같은 형식).
신선도: 뉴스·공시는 KRX 휴장과 무관해 평일 기준(app.data_status.weekdays_behind)이다. 수집이 전체적으로 멈췄는지(last_collected_at,
전 종목의 마지막 저장 시각)와 이 종목의 최신 항목(company_latest_*)을 따로 준다. 이 종목 기사가 오래된 것은 수집 지연이 아닐 수 있다.
"""

from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session, aliased

from app.data_status import KST, last_attempt, last_weekday, weekdays_behind
from app.ingest_validation import _CORRECTION_RE, is_correction
from app.models import Company, Disclosure, News
from app.pagination import page_meta

DEFAULT_LIMIT = 20
MAX_LIMIT = 50
STALE_WEEKDAYS = 2  # 마지막 수집이 최근 평일보다 이만큼 이상 늦으면 stale(GET /api/status의 시세 기준과 같은 2)
REFRESH_STEP = "news"  # refresh_data에서 뉴스와 공시를 같이 모으는 단계(scripts/refresh_data.py STEP_ORDER)
_RCPT_RE = re.compile(r"rcptNo=(\d+)")
# 정정 공시 목록 필터를 SQL에서 하려고 app.ingest_validation._CORRECTION_RE를 PostgreSQL 정규식으로 그대로 옮긴다.
# 파이썬 쪽(is_correction)은 제목을 strip()한 뒤 맞추므로 SQL은 앞 공백을 허용한다. 패턴 문법((?:…), \s)은 PostgreSQL ARE도 같다.
CORRECTION_SQL_RE = r"^\s*" + _CORRECTION_RE.pattern.removeprefix("^")
ETF_DISCLOSURE_MESSAGE = "ETF는 DART 공시 수집 대상이 아니어서 공시 목록이 없습니다. 뉴스 목록(GET /api/stocks/{ticker}/news)을 이용하세요."


def kst_range(date_from: date | None, date_to: date | None) -> tuple[datetime | None, datetime | None]:
    """(시작 이상, 끝 미만) KST 시각. date_from이 date_to보다 늦으면 ValueError."""
    if date_from and date_to and date_from > date_to:
        raise ValueError("date_from이 date_to보다 늦습니다.")
    start = datetime.combine(date_from, time.min, KST) if date_from else None
    end = datetime.combine(date_to + timedelta(days=1), time.min, KST) if date_to else None
    return start, end


def _iso(ts: datetime | None) -> str | None:
    return ts.astimezone(KST).isoformat() if ts else None


def rcept_no(url: str | None) -> str | None:
    m = _RCPT_RE.search(url or "")
    return m.group(1) if m else None


def freshness(last_collected: datetime | None, company_latest: datetime | None, today: date,
              last_collection: dict | None = None) -> dict:
    """last_collected: 그 테이블 전체의 마지막 저장 시각. company_latest: 이 종목의 가장 최근 게시·접수 시각.
    last_collection: refresh_data 뉴스·공시 단계의 마지막 시도(app.data_status.last_attempt). status=failed면 수집 실패라 목록이 비거나
    오래됐을 수 있고, success인데 items가 비면 이 종목에 저장된 항목이 원래 없는 것이다."""
    lag = weekdays_behind(last_collected.astimezone(KST).date(), last_weekday(today)) if last_collected else None
    return {"last_collected_at": _iso(last_collected), "collection_lag_weekdays": lag,
            "stale": lag is None or lag >= STALE_WEEKDAYS, "company_latest_at": _iso(company_latest),
            "last_collection": last_collection}


def _base(company: Company, total: int, limit: int, offset: int, date_from: date | None, date_to: date | None, order: str) -> dict:
    return {"ticker": company.ticker, "company_name": company.name, "asset_type": company.asset_type,
            "total": total, "limit": limit, "offset": offset, **page_meta(total, limit, offset),
            "filters": {"date_from": date_from, "date_to": date_to, "order": order}}


def _window(q, column, start, end):
    if start is not None:
        q = q.where(column >= start)
    if end is not None:
        q = q.where(column < end)
    return q


def news_item(row, related: list[str]) -> dict:
    return {"id": row.id, "title": row.title, "summary": row.content or None, "source": row.source, "url": row.url,
            "published_at": _iso(row.published_at), "collected_at": _iso(row.created_at),
            "is_relevant": row.is_relevant, "related_tickers": related}


def news_page(db: Session, company: Company, limit: int, offset: int, date_from: date | None, date_to: date | None,
              order: str, today: date) -> dict:
    """쿼리 5개: 건수, 페이지, 같은 URL의 다른 종목, 신선도(전체 마지막 저장 + 이 종목 최신), 마지막 수집 시도."""
    start, end = kst_range(date_from, date_to)
    # 같은 종목에 같은 URL이 두 번 저장됐으면(유니크 제약이 없다, DATA_QUALITY_AND_TRADING_CALENDAR.md) id가 가장 큰 행 하나만 보인다.
    # 다른 종목의 같은 URL 행은 건드리지 않는다(종목별 관계 유지).
    newer = aliased(News)
    dup = select(newer.id).where(newer.company_id == company.id, newer.url == News.url, newer.id > News.id).exists()
    cond = [News.company_id == company.id, News.is_relevant.isnot(False), ~dup]
    total = db.execute(_window(select(func.count()).select_from(News).where(*cond), News.published_at, start, end)).scalar_one()
    # 게시 시각이 없는 행은 양쪽 정렬 모두 맨 뒤(PostgreSQL 기본은 DESC에서 NULL이 맨 앞이라 최신순 첫 줄에 올라온다)
    sort = ([News.published_at.asc().nulls_last(), News.id.asc()] if order == "asc"
            else [News.published_at.desc().nulls_last(), News.id.desc()])
    rows = db.execute(_window(select(News.id, News.title, News.content, News.source, News.url, News.published_at, News.created_at,
                                     News.is_relevant)
                              .where(*cond), News.published_at, start, end).order_by(*sort).limit(limit).offset(offset)).all()
    related: dict[str, list[str]] = {}
    urls = sorted({r.url for r in rows if r.url})
    if urls:
        for url, ticker in db.execute(select(News.url, Company.ticker).join(Company, Company.id == News.company_id)
                                      .where(News.url.in_(urls), News.company_id != company.id)
                                      .distinct().order_by(News.url, Company.ticker)).all():
            related.setdefault(url, []).append(ticker)
    last, latest = db.execute(select(select(func.max(News.created_at)).scalar_subquery(),
                                     select(func.max(News.published_at)).where(*cond).scalar_subquery())).one()
    return {**_base(company, total, limit, offset, date_from, date_to, order),
            "freshness": freshness(last, latest, today, last_attempt(db, REFRESH_STEP)),
            "items": [news_item(r, related.get(r.url, [])) for r in rows]}


def disclosure_item(row) -> dict:
    at = row.disclosed_at.astimezone(KST) if row.disclosed_at else None
    return {"id": row.id, "title": row.title, "disclosure_type": row.disclosure_type, "rcept_no": rcept_no(row.source_url),
            "disclosed_date": at.date() if at else None, "disclosed_at": at.isoformat() if at else None,
            "source_url": row.source_url, "is_correction": is_correction(row.title), "collected_at": _iso(row.created_at)}


def disclosure_page(db: Session, company: Company, limit: int, offset: int, date_from: date | None, date_to: date | None,
                    order: str, correction: str, today: date) -> dict:
    """쿼리 4개: 건수, 페이지, 신선도, 마지막 수집 시도. ETF는 쿼리 2개(전체 마지막 저장 시각, 수집 시도)만 하고 supported=false.
    공시는 DART 접수번호가 행마다 달라 같은 종목에 같은 원문 URL이 두 번 저장되면 그것은 중복이다. 그 경우 id가 큰 행 하나만 보인다."""
    start, end = kst_range(date_from, date_to)
    if company.asset_type == "etf":
        last = db.execute(select(func.max(Disclosure.created_at))).scalar()
        base = _base(company, 0, limit, offset, date_from, date_to, order)
        base["filters"]["correction"] = correction
        return {**base, "supported": False, "message": ETF_DISCLOSURE_MESSAGE.replace("{ticker}", company.ticker),
                "freshness": freshness(last, None, today, last_attempt(db, REFRESH_STEP)), "items": []}
    newer = aliased(Disclosure)
    dup = select(newer.id).where(newer.company_id == company.id, newer.source_url == Disclosure.source_url,
                                 newer.id > Disclosure.id).exists()
    cond = [Disclosure.company_id == company.id, ~dup]
    if correction == "only":
        cond.append(Disclosure.title.op("~")(CORRECTION_SQL_RE))
    elif correction == "exclude":
        cond.append(~Disclosure.title.op("~")(CORRECTION_SQL_RE))
    total = db.execute(_window(select(func.count()).select_from(Disclosure).where(*cond), Disclosure.disclosed_at, start, end)).scalar_one()
    sort = ([Disclosure.disclosed_at.asc().nulls_last(), Disclosure.id.asc()] if order == "asc"
            else [Disclosure.disclosed_at.desc().nulls_last(), Disclosure.id.desc()])
    rows = db.execute(_window(select(Disclosure.id, Disclosure.title, Disclosure.disclosure_type, Disclosure.source_url,
                                     Disclosure.disclosed_at, Disclosure.created_at).where(*cond),
                              Disclosure.disclosed_at, start, end).order_by(*sort).limit(limit).offset(offset)).all()
    last, latest = db.execute(select(select(func.max(Disclosure.created_at)).scalar_subquery(),
                                     select(func.max(Disclosure.disclosed_at)).where(Disclosure.company_id == company.id)
                                     .scalar_subquery())).one()
    base = _base(company, total, limit, offset, date_from, date_to, order)
    base["filters"]["correction"] = correction
    return {**base, "supported": True, "message": None, "freshness": freshness(last, latest, today, last_attempt(db, REFRESH_STEP)),
            "items": [disclosure_item(r) for r in rows]}

