"""뉴스·공시 누락 구간 복구(62차, 2026-11-15). scripts/recover_news_disclosures.py가 쓴다.

## 왜 따로 두나
기존 수집(app.collectors.fetch_and_save_news)은 실행마다 종목당 최신 기사 10건만 저장하고 다음 페이지를 받지 않는다(61차).
수집이 며칠 멈춘 뒤에는 그 사이 기사가 채워지지 않는다. 이 모듈은 네이버 뉴스 검색을 날짜순으로 페이지를 넘겨 받아,
지정한 기간(KST)의 기사를 저장한다. 걸러내기·검증·관련성 판정은 기존 수집과 같은 함수를 쓴다.

## 네이버 뉴스 검색 범위(네이버 클라우드 API 문서 기준, 62차 보고서 3절)
- display 1~100, start 1~1000, sort=date(최신순). 한 검색어로 볼 수 있는 결과는 최신 1,000건(100건 x 10페이지)이다.
- 하루 호출 한도 25,000회(같은 문서). 이 프로젝트가 쓰는 openapi.naver.com 엔드포인트와 같은 한도인지는 [추정].

## 멈추는 조건(paginate)
- reached_since: 받은 페이지의 가장 오래된 기사가 시작 시각보다 이르다(그 앞은 기간 밖).
- end_of_results: 빈 페이지이거나 display보다 적게 왔다(더 없다).
- max_window: 다음 start가 1,000을 넘는다(검색 범위 끝). 시작 시각에 닿지 못했으면 truncated=True(기간 앞부분은 받을 수 없다).
- max_pages: 지정한 페이지 수에 닿았다(요청량 상한). 시작 시각에 닿지 못했으면 truncated=True.

## 저장(store_news)
- 기존 수집과 같다: 비금융 도메인 제외, ETF는 이름이 나오는 기사만, 저장 전 검증(V.check_news) 뒤 격리·경고 기록, is_relevant 판정.
- 다른 점: 건수를 10건으로 자르지 않는다. 기간 밖 기사는 저장하지 않는다. 같은 응답 안·페이지 사이의 같은 URL은 한 번만 본다.
- 이미 저장된 (종목, URL)은 다시 저장하지 않는다(기존 행 유지). dry_run이면 DB에 쓰지 않는다(격리 기록도 쓰지 않는다).
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import date, datetime, time as dtime, timedelta
from email.utils import parsedate_to_datetime
from typing import Callable
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")
PAGE_SIZE = 100  # 네이버 display 최대
MAX_START = 1000  # 네이버 start 최대
MAX_PAGES = (MAX_START - 1) // PAGE_SIZE + 1  # start 1, 101, ..., 901 = 10페이지(1,000건)
NAVER_DAILY_LIMIT = 25_000  # 네이버 검색 API 하루 호출 한도(문서 값)
DEFAULT_NAVER_BUDGET = 20_000  # 한 번 실행에서 쓸 상한(한도 안에 여유를 둔다)

REACHED_SINCE, END_OF_RESULTS, MAX_WINDOW, MAX_PAGES_HIT, ERROR = "reached_since", "end_of_results", "max_window", "max_pages", "error"


def kst_window(start: date, end: date) -> tuple[datetime, datetime]:
    """[start 00:00 KST, end 다음 날 00:00 KST)."""
    if start > end:
        raise ValueError("start가 end보다 늦습니다")
    return datetime.combine(start, dtime.min, KST), datetime.combine(end + timedelta(days=1), dtime.min, KST)


def item_url(it: dict) -> str | None:
    return it.get("link") or it.get("originallink") or None


def item_time(it: dict) -> datetime | None:
    try:
        return parsedate_to_datetime(it.get("pubDate") or "")
    except (TypeError, ValueError):
        return None


@dataclass
class PageScan:
    items: list[dict] = field(default_factory=list)  # 기간 안(또는 게시 시각을 못 읽은) 기사, URL 중복 없음
    pages: int = 0
    seen: int = 0  # 받은 기사 수(기간 밖 포함)
    out_of_range: int = 0  # 기간 밖이라 버린 수(시작 전·끝 뒤)
    duplicate_in_scan: int = 0  # 페이지 사이에 같은 URL
    stop: str = ""
    truncated: bool = False  # 시작 시각에 닿기 전에 멈췄다(기간 앞부분을 받지 못했다)
    oldest_seen: datetime | None = None
    error: str | None = None


def paginate(fetch: Callable[..., list[dict]], query: str, since: datetime, until: datetime, max_pages: int = MAX_PAGES,
             delay: float = 0.0, sleep: Callable[[float], None] = time.sleep) -> PageScan:
    """fetch(query, display=, sort="date", start=)로 최신순 페이지를 넘긴다. 기간 [since, until)의 기사만 남긴다.
    fetch가 예외를 내면 그때까지 받은 것을 담아 stop=error로 돌려준다(호출자가 종목 실패로 기록하고 다시 실행할 수 있다)."""
    max_pages = max(1, min(max_pages, MAX_PAGES))
    out, urls = PageScan(), set()
    for page in range(max_pages):
        start = 1 + page * PAGE_SIZE
        if page and delay:
            sleep(delay)
        try:
            items = fetch(query, display=PAGE_SIZE, sort="date", start=start)
        except Exception as e:  # noqa: BLE001 - 종목 단위로 실패를 기록한다
            out.stop, out.error = ERROR, f"{type(e).__name__}: {str(e)[:200]}"
            return out
        out.pages += 1
        if not items:
            out.stop = END_OF_RESULTS
            return out
        oldest = None
        for it in items:
            out.seen += 1
            t = item_time(it)
            if t is not None:
                oldest = t if oldest is None or t < oldest else oldest
                if t < since or t >= until:
                    out.out_of_range += 1
                    continue
            url = item_url(it)
            if url in urls:
                out.duplicate_in_scan += 1
                continue
            urls.add(url)
            out.items.append(it)
        if oldest is not None:
            out.oldest_seen = oldest if out.oldest_seen is None or oldest < out.oldest_seen else out.oldest_seen
        if oldest is not None and oldest < since:
            out.stop = REACHED_SINCE
            return out
        if len(items) < PAGE_SIZE:
            out.stop = END_OF_RESULTS
            return out
    out.stop = MAX_WINDOW if max_pages == MAX_PAGES else MAX_PAGES_HIT
    out.truncated = True
    return out


@dataclass
class StoreResult:
    candidates: int = 0  # 걸러내기 전 기사 수(기간 안)
    non_financial: int = 0
    etf_name_missing: int = 0
    existing: int = 0  # 이미 저장된 (종목, URL)
    new: int = 0  # 새로 저장(또는 dry_run이면 저장 예정)
    relevant_new: int = 0
    quarantined: int = 0
    warned: int = 0


def store_news(db, company, items: list[dict], *, dry_run: bool, listed_names: list[str] | None = None,
               etf_names: list[str] | None = None, now: datetime | None = None) -> StoreResult:
    """기간 안 기사(items)를 기존 수집과 같은 규칙으로 걸러 저장한다. dry_run이면 쓰지 않는다. 쓰는 표: news, data_ingestion_quarantine."""
    from sqlalchemy import select

    from app import collectors as C, ingest_validation as V, quarantine
    from app.etf import mentions_etf_name
    from app.models import Company, News
    from app.news_relevance import is_relevant_news

    res = StoreResult(candidates=len(items))
    if listed_names is None:
        listed_names = [n for (n,) in db.execute(select(Company.name))]
    kept = []
    for it in items:
        if C._is_non_financial_item(it):
            res.non_financial += 1
            continue
        if company.asset_type == "etf":
            others = etf_names if etf_names is not None else [
                n for (n,) in db.execute(select(Company.name).where(Company.asset_type == "etf", Company.name != company.name))]
            etf_names = others
            text = f"{C._clean_text(it.get('title', ''))} {C._clean_text(it.get('description', ''))}"
            if not mentions_etf_name(text, company.name, others):
                res.etf_name_missing += 1
                continue
        kept.append(it)
    urls = [item_url(it) for it in kept if item_url(it)]
    existing = set()
    for i in range(0, len(urls), 500):
        chunk = urls[i:i + 500]
        existing |= {u for (u,) in db.execute(select(News.url).where(News.company_id == company.id, News.url.in_(chunk)))}
    candidates = []
    for it in kept:
        url = item_url(it)
        if url in existing:
            res.existing += 1
            continue
        candidates.append({
            "title": C._clean_text(it.get("title") or ""), "content": C._clean_text(it.get("description") or ""),
            "source": urlparse(it.get("originallink") or url or "").netloc, "url": url, "published_at": item_time(it),
        })
    checked = V.check_news(company.ticker, candidates, now=now)
    keep, bad = V.split(checked)
    res.quarantined, res.warned = len(bad), sum(c.status == V.WARNING for c in keep)
    rows = [
        News(company_id=company.id, title=c.record["title"], content=c.record["content"], source=c.record["source"],
             url=c.record["url"], published_at=c.record["published_at"],
             is_relevant=is_relevant_news(company.name, company.ticker, company.asset_type, c.record["title"],
                                          c.record["content"], listed_names))
        for c in keep
    ]
    res.new, res.relevant_new = len(rows), sum(1 for r in rows if r.is_relevant is not False)
    if dry_run:
        db.rollback()
        return res
    quarantine.record("news", checked, {"ticker": company.ticker, "via": "news_recovery"})
    for r in rows:
        db.add(r)
    db.commit()
    return res


def news_query(company) -> str:
    """기존 수집과 같은 검색어(주식은 collectors.news_search_query, ETF는 이름 그대로)."""
    from app import collectors as C

    return company.name if company.asset_type == "etf" else C.news_search_query(company.name)


def disclosure_days(start: date, today: date) -> int:
    """DART 조회 기간(일, 오늘 포함). start부터 오늘까지. 기존 수집(90일)보다 길게 잡지 않는다."""
    return max(1, min(90, (today - start).days + 1))
