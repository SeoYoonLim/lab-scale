"""데이터 갱신 모니터링: 소스별 최신성·마지막 수집·품질 상태와 운영 보고서(2026-11-07).

수집했다는 사실(마지막 실행 결과)과 데이터가 분석에 쓸 수 있는 상태라는 사실(실제 데이터의 기준일·품질)을 따로 계산한다.
- status: 운영 상태. unsupported | disabled | running | never_run | failed | partial | stale | healthy (우선순위 순)
- usable_for_analysis: 데이터 기준일이 최신성 기준 안에 있고 품질 검사에 error가 없을 때만 True. 마지막 실행이 실패해도
  이전에 받은 데이터가 최신이면 True다(최근 실패 때문에 멀쩡한 데이터가 사라진 것처럼 보이지 않게).

DB는 읽기만 한다(SELECT). 상태 판정(evaluate_source)과 보고서 문장은 DB 없이 테스트한다(tests/test_data_monitor.py).
영업일은 평일 기준이다(KRX 공휴일 달력 없음: app/data_status와 같은 한계). 그래서 최신성 기준은 지연 2영업일 이상을 stale로 본다
(공휴일 하루 때문에 정상 데이터가 stale이 되지 않게, GET /api/status·look-through와 같은 기준).
"""

from __future__ import annotations

import json
import logging
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Callable, Protocol
from zoneinfo import ZoneInfo

from sqlalchemy import text

from app.data_status import STALE_BUSINESS_DAYS, business_days_behind, last_business_day

KST = ZoneInfo("Asia/Seoul")
logger = logging.getLogger(__name__)

STATUSES = ("healthy", "stale", "partial", "failed", "running", "never_run", "disabled", "unsupported")
QUALITY = ("ok", "warning", "error")
MARKET_CLOSE = (15, 30)


@dataclass(frozen=True)
class Source:
    key: str
    label: str
    step: str | None  # refresh_data 단계(없으면 이 실행기로 수집하지 않음)
    freshness: str  # trading_close | business_day | etf_holdings | derived | quarterly | none
    schedule: str  # 권장 실행 시점(사람이 읽는 문장)
    origin: str  # 수집 출처·방식
    enabled: bool = True
    supported: bool = True
    stale_after: int = STALE_BUSINESS_DAYS  # 지연 영업일이 이 값 이상이면 stale
    note: str = ""


SOURCES: tuple[Source, ...] = (
    Source("stock_price", "국내 주식 종가", "prices", "trading_close", "거래일 16:30 KST 이후 1회",
           "FinanceDataReader(KRX), scripts/collect_prices --universe universe_top300.json, stock_price upsert"),
    Source("etf_price", "ETF 종가", "prices", "trading_close", "거래일 16:30 KST 이후 1회(주식과 같은 단계)",
           "FinanceDataReader(KRX), scripts/collect_prices --universe universe_etf50.json"),
    Source("market_index", "시장 지수(KS11·KQ11)", "index", "trading_close", "거래일 16:30 KST 이후 1회",
           "FinanceDataReader -> 별칭·Yahoo 폴백(collectors.MARKET_INDEX_SOURCES), market_index upsert"),
    Source("exchange_rate", "환율(USD/KRW)", "fx", "trading_close", "거래일 16:30 KST 이후 1회",
           "FinanceDataReader, exchange_rate upsert. 출처가 전일 값까지 주는 날이 있어 1영업일 지연은 정상 범위"),
    Source("news", "뉴스", "news", "business_day", "평일 하루 1~2회(네이버 검색 API 일일 한도 안에서)",
           "네이버 뉴스 검색 API, scripts/collect_universe(종목별 url 중복 검사)"),
    Source("disclosure", "DART 공시", "news", "business_day", "평일 18:30 KST 이후 1회(뉴스와 같은 단계)",
           "OpenDART list.json, scripts/collect_universe(접수번호가 든 source_url 중복 검사). 뉴스와 한 단계로 수집된다",
           note="--source dart는 news 단계를 실행한다(공시만 따로 받는 기존 옵션이 없다)"),
    Source("embeddings", "뉴스·공시 임베딩", "embed", "derived", "news 단계 뒤(같은 실행)",
           "Ollama bge-m3, app.embeddings(임베딩이 없는 행만)"),
    Source("disclosure_content", "공시 본문", "content", "derived", "news 단계 뒤(같은 실행)",
           "OpenDART document.xml, scripts/collect_disclosure_content --apply(본문이 없는 행만)"),
    Source("etf_holdings", "ETF 구성종목(KODEX·TIGER)", "etf_holdings", "etf_holdings",
           "평일 저녁 1회(운용사가 영업일마다 공시, 전체 3~4분, 요청 간 3초)",
           "운용사 공시 PDF/구성종목 파일, app.etf_holdings_collect((ETF, 기준일) 단위 교체)",
           note="기본 단계가 아니다: --etf-holdings 또는 --source etf_holdings"),
    Source("earnings", "분기 실적(DART 주요 계정)", None, "quarterly", "분기 보고서 공시 뒤 수동(종목당 요청 12회, 약 3,600회)",
           "OpenDART fnlttSinglAcnt, refresh_data --earnings-only", enabled=False,
           note="자동 실행 대상이 아니다(분기 1회 수동)"),
    Source("kospi200", "KOSPI200 지수", None, "none", "-", "수집기 없음", enabled=False, supported=False,
           note="수집기·검증된 출처가 없다(market_index에는 KS11·KQ11만)"),
    Source("other_issuer_etf_holdings", "KODEX·TIGER 외 ETF 구성종목", None, "none", "-", "수집기 없음", enabled=False,
           supported=False, note="RISE·ACE·SOL·HANARO·PLUS·TIME 파서 없음"),
    Source("interest_rate", "금리", None, "none", "-", "수집기 없음", enabled=False, supported=False,
           note="쓸 만한 무료 출처를 찾지 못함(README FR-07)"),
    Source("overseas_stock", "해외 주식 시세", None, "none", "-", "수집기 없음", enabled=False, supported=False,
           note="company에 해외 종목이 없다"),
)
SOURCE_BY_KEY = {s.key: s for s in SOURCES}


# ---------- 기대 기준일 ----------


def expected_close_date(now: datetime) -> date:
    """종가가 있어야 할 가장 최근 평일. 평일 15:30(KST) 전이면 직전 평일(장중 값은 종가가 아니다)."""
    now = now.astimezone(KST)
    d = last_business_day(now.date())
    if d == now.date() and (now.hour, now.minute) < MARKET_CLOSE:
        d = last_business_day(d - timedelta(days=1))
    return d


def expected_as_of(src: Source, now: datetime) -> date | None:
    if src.freshness == "trading_close":
        return expected_close_date(now)
    if src.freshness in ("business_day", "etf_holdings"):
        return last_business_day(now.astimezone(KST).date())
    return None


# ---------- 상태 판정(순수) ----------


@dataclass
class SourceFacts:
    """한 소스에 대해 DB에서 읽은 사실. data_as_of가 None이면 데이터가 없다."""
    data_as_of: date | None = None
    record_count: int | None = None
    extra: dict = field(default_factory=dict)


def evaluate_source(src: Source, facts: SourceFacts, history: dict | None, quality: dict | None, now: datetime,
                    running_steps: set[str] | None = None) -> dict:
    """소스 하나의 모니터링 행. history는 refresh_runs.step_history의 그 단계 값, quality는 check_quality의 그 소스 값."""
    history = history or {}
    quality = quality or {"status": None, "checks": []}
    last = history.get("last_attempt") or {}
    last_ok = history.get("last_success") or {}
    last_fail = history.get("last_failure") or {}
    exp = expected_as_of(src, now)
    lag = business_days_behind(facts.data_as_of, exp) if (facts.data_as_of and exp) else None
    stale = lag is not None and lag >= src.stale_after
    reasons: list[str] = []

    if not src.supported:
        status = "unsupported"
        reasons.append(src.note or "수집기 없음")
    elif not src.enabled:
        status = "disabled"
        reasons.append(src.note or "자동 실행 대상이 아님")
    elif src.step and running_steps and src.step in running_steps:
        status = "running"
    elif not last and facts.data_as_of is None and not facts.record_count:
        status = "never_run"
        reasons.append("수집 기록과 데이터가 없음")
    elif last.get("status") == "failed":
        status = "failed"
        reasons.append(f"마지막 실행 실패({last.get('error_code') or last.get('error_type') or '원인 미상'})")
    elif last.get("status") == "partial":
        status = "partial"
        reasons.append("마지막 실행에서 일부 대상 실패")
    elif stale:
        status = "stale"
    elif src.freshness in ("trading_close", "business_day", "etf_holdings") and facts.data_as_of is None:
        status = "stale"
        reasons.append("데이터 없음")
    else:
        status = "healthy"
    if stale:
        reasons.append(f"데이터 기준일 {facts.data_as_of}이 기대 기준일 {exp}보다 {lag}영업일 늦음")
    elif lag == 1:
        reasons.append("1영업일 이전 데이터(정상 범위: 출처 갱신 시각·공휴일)")
    if quality.get("status") == "error":
        reasons.append("품질 검사 error")
    usable = (src.supported and facts.data_as_of is not None and not stale and quality.get("status") != "error") \
        if src.freshness in ("trading_close", "business_day", "etf_holdings") else \
        (src.supported and quality.get("status") != "error" and (facts.record_count or 0) > 0)
    warnings = [c for c in quality.get("checks", []) if c["severity"] == "warning" and c["count"]]
    return {
        "source": src.key, "label": src.label, "step": src.step, "status": status,
        "usable_for_analysis": bool(usable) if src.supported else False,
        "last_run_at": _iso(last.get("finished_at") or last.get("started_at")),
        "last_run_status": last.get("status"),
        "last_success_at": _iso(last_ok.get("finished_at")),
        "last_failure_at": _iso(last_fail.get("finished_at") or last_fail.get("started_at")),
        "last_failure_reason": (last_fail.get("error_code") or last_fail.get("error_type")) if last_fail else None,
        "data_as_of": facts.data_as_of.isoformat() if facts.data_as_of else None,
        "expected_as_of": exp.isoformat() if exp else None,
        "lag_business_days": lag,
        "duration_ms": last.get("duration_ms"),
        "records_inserted": last.get("rows_added"),
        "records_updated": None,  # 기존 수집기가 갱신 건수를 따로 세지 않는다(upsert)
        "records_skipped": None,
        "record_count": facts.record_count,
        "error_code": last.get("error_code") if last.get("status") in ("failed", "partial") else None,
        "retry_count": last.get("retry_count"),
        "quality_status": quality.get("status"),
        "warning_count": sum(c["count"] for c in warnings),
        "quality_checks": quality.get("checks", []),
        "freshness_policy": src.freshness, "stale_after_business_days": src.stale_after,
        "schedule": src.schedule, "origin": src.origin, "reasons": reasons, "note": src.note,
        **({"extra": facts.extra} if facts.extra else {}),
    }


def _iso(v) -> str | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return (v if v.tzinfo else v.replace(tzinfo=timezone.utc)).astimezone(KST).isoformat(timespec="seconds")
    return str(v)


# ---------- DB 사실 수집(읽기 전용) ----------


def _one(db, sql: str, **params):
    return db.execute(text(sql), params).scalar()


def _kst_date(ts) -> date | None:
    if ts is None:
        return None
    return (ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)).astimezone(KST).date()


def collect_facts(db) -> dict[str, SourceFacts]:
    f: dict[str, SourceFacts] = {}
    for key, asset in (("stock_price", "stock"), ("etf_price", "etf")):
        row = db.execute(text("SELECT max(p.price_date), count(*) FROM stock_price p JOIN company c ON c.id = p.company_id "
                              "WHERE c.asset_type = :a"), {"a": asset}).first()
        f[key] = SourceFacts(row[0], row[1])
    rows = db.execute(text("SELECT index_code, max(price_date), count(*) FROM market_index GROUP BY index_code")).all()
    f["market_index"] = SourceFacts(min((r[1] for r in rows), default=None), sum(r[2] for r in rows),
                                    {"per_index": {r[0]: r[1].isoformat() for r in rows}})
    rows = db.execute(text("SELECT pair_code, max(price_date), count(*) FROM exchange_rate GROUP BY pair_code")).all()
    f["exchange_rate"] = SourceFacts(min((r[1] for r in rows), default=None), sum(r[2] for r in rows),
                                     {"per_pair": {r[0]: r[1].isoformat() for r in rows}})
    row = db.execute(text("SELECT max(published_at), count(*), max(created_at) FROM news")).first()
    f["news"] = SourceFacts(_kst_date(row[0]), row[1], {"last_collected_at": _iso(row[2])})
    row = db.execute(text("SELECT max(disclosed_at), count(*), max(created_at) FROM disclosure")).first()
    f["disclosure"] = SourceFacts(_kst_date(row[0]), row[1], {"last_collected_at": _iso(row[2])})
    row = db.execute(text("SELECT (SELECT count(*) FROM news WHERE embedding IS NOT NULL) + "
                          "(SELECT count(*) FROM disclosure WHERE embedding IS NOT NULL), "
                          "(SELECT count(*) FROM news WHERE embedding IS NULL) + (SELECT count(*) FROM disclosure WHERE embedding IS NULL)")).first()
    f["embeddings"] = SourceFacts(None, row[0], {"missing": row[1]})
    row = db.execute(text("SELECT count(content), count(*) - count(content) FROM disclosure")).first()
    f["disclosure_content"] = SourceFacts(None, row[0], {"missing": row[1]})
    rows = db.execute(text("SELECT c.ticker, max(h.as_of_date) FROM etf_holding h JOIN company c ON c.id = h.company_id "
                           "GROUP BY c.ticker")).all()
    # scripts/collect_etf_holdings로 따로 받은 경우 data_refresh_log에 기록이 없다: 실제 마지막 수집 시각은 collected_at으로 보인다
    f["etf_holdings"] = SourceFacts(min((r[1] for r in rows), default=None), _one(db, "SELECT count(*) FROM etf_holding"),
                                    {"etfs": len(rows), "newest": max((r[1] for r in rows), default=None) and
                                     max(r[1] for r in rows).isoformat(),
                                     "last_collected_at": _iso(_one(db, "SELECT max(collected_at) FROM etf_holding"))})
    row = db.execute(text("SELECT max(disclosed_date), count(*) FROM quarterly_earnings")).first()
    f["earnings"] = SourceFacts(row[0], row[1])
    for s in SOURCES:
        f.setdefault(s.key, SourceFacts())
    return f


# ---------- 품질 검사(읽기 전용) ----------

# 품질 검사 이름 -> (소스, 심각도, SQL). SQL은 문제 건수 하나를 돌려준다. :today, :now, :since(60일 전)를 쓴다.
# error = 그 데이터를 분석에 쓰면 안 되는 문제(값 오류·미래 날짜·중복), warning = 확인이 필요한 이상.
QUALITY_CHECKS: tuple[tuple[str, str, str, str, str], ...] = (
    ("stock_price", "close_null_or_nonpositive", "error", "종가 null 또는 0 이하",
     "SELECT count(*) FROM stock_price WHERE close_price IS NULL OR close_price <= 0"),
    ("stock_price", "future_date", "error", "오늘보다 뒤의 기준일",
     "SELECT count(*) FROM stock_price WHERE price_date > :today"),
    ("stock_price", "duplicate_company_date", "error", "같은 종목·날짜 중복",
     "SELECT count(*) FROM (SELECT 1 FROM stock_price GROUP BY company_id, price_date HAVING count(*) > 1) t"),
    ("stock_price", "extreme_move", "warning", "주식 하루 변동 ±30% 초과(최근 60일, 가격제한폭 밖)",
     "SELECT count(*) FROM (SELECT p.close_price / NULLIF(lag(p.close_price) OVER (PARTITION BY p.company_id ORDER BY p.price_date), 0) - 1 AS r "
     "FROM stock_price p JOIN company c ON c.id = p.company_id WHERE c.asset_type = 'stock' AND p.price_date >= :since) t "
     "WHERE abs(r) > 0.30"),
    ("stock_price", "behind_latest", "warning", "최근 60일 시세가 있는데 마지막 기준일이 전체 최신일보다 이른 종목",
     "SELECT count(*) FROM (SELECT company_id, max(price_date) m FROM stock_price WHERE price_date >= :since GROUP BY company_id) t "
     "WHERE m < (SELECT max(price_date) FROM stock_price)"),
    ("stock_price", "company_without_price", "warning", "시세가 하나도 없는 종목(company 등록만 됨)",
     "SELECT count(*) FROM company c WHERE NOT EXISTS (SELECT 1 FROM stock_price p WHERE p.company_id = c.id)"),
    ("market_index", "close_nonpositive", "error", "지수 0 이하",
     "SELECT count(*) FROM market_index WHERE close_price IS NULL OR close_price <= 0"),
    ("market_index", "future_date", "error", "오늘보다 뒤의 기준일", "SELECT count(*) FROM market_index WHERE price_date > :today"),
    ("market_index", "index_behind_other", "warning", "한 지수의 마지막 날짜가 다른 지수보다 이름",
     "SELECT count(*) FROM (SELECT index_code, max(price_date) m FROM market_index GROUP BY index_code) t "
     "WHERE m < (SELECT max(price_date) FROM market_index)"),
    ("market_index", "price_day_missing_in_index", "warning", "최근 60일 주가 거래일인데 KS11 지수가 없는 날",
     "SELECT count(*) FROM (SELECT DISTINCT price_date FROM stock_price WHERE price_date >= :since) p "
     "WHERE p.price_date <= (SELECT max(price_date) FROM market_index WHERE index_code = 'KS11') "
     "AND NOT EXISTS (SELECT 1 FROM market_index m WHERE m.index_code = 'KS11' AND m.price_date = p.price_date)"),
    ("market_index", "index_day_missing_in_price", "warning", "최근 60일 KS11 거래일인데 주가가 하나도 없는 날",
     "SELECT count(*) FROM market_index m WHERE m.index_code = 'KS11' AND m.price_date >= :since "
     "AND m.price_date <= (SELECT max(price_date) FROM stock_price) "
     "AND NOT EXISTS (SELECT 1 FROM stock_price p WHERE p.price_date = m.price_date)"),
    ("exchange_rate", "rate_nonpositive", "error", "환율 0 이하",
     "SELECT count(*) FROM exchange_rate WHERE close_price IS NULL OR close_price <= 0"),
    ("exchange_rate", "future_date", "error", "오늘보다 뒤의 기준일", "SELECT count(*) FROM exchange_rate WHERE price_date > :today"),
    ("exchange_rate", "extreme_move", "warning", "하루 변동 ±5% 초과(최근 60일)",
     "SELECT count(*) FROM (SELECT close_price / NULLIF(lag(close_price) OVER (PARTITION BY pair_code ORDER BY price_date), 0) - 1 r "
     "FROM exchange_rate WHERE price_date >= :since) t WHERE abs(r) > 0.05"),
    ("exchange_rate", "weekday_gap", "warning", "최근 60일 KS11 거래일인데 환율이 없는 날(마지막 환율 날짜까지)",
     "SELECT count(*) FROM market_index m WHERE m.index_code = 'KS11' AND m.price_date >= :since "
     "AND m.price_date <= (SELECT max(price_date) FROM exchange_rate) "
     "AND NOT EXISTS (SELECT 1 FROM exchange_rate e WHERE e.price_date = m.price_date)"),
    ("news", "duplicate_company_url", "warning", "같은 종목·같은 url 기사 중복",
     "SELECT count(*) FROM (SELECT 1 FROM news WHERE url IS NOT NULL GROUP BY company_id, url HAVING count(*) > 1) t"),
    ("news", "published_at_missing", "warning", "발행 시각 없음", "SELECT count(*) FROM news WHERE published_at IS NULL"),
    ("news", "url_missing", "warning", "원문 링크 없음", "SELECT count(*) FROM news WHERE url IS NULL OR url = ''"),
    ("news", "company_missing", "warning", "종목 연결 없음(종목이 지워져 SET NULL)", "SELECT count(*) FROM news WHERE company_id IS NULL"),
    ("news", "future_published", "error", "지금보다 1시간 넘게 뒤의 발행 시각",
     "SELECT count(*) FROM news WHERE published_at > :now + interval '1 hour'"),
    ("news", "old_article_collected", "warning", "최근 7일에 저장했는데 발행이 30일 넘게 전인 기사",
     "SELECT count(*) FROM news WHERE created_at >= :now - interval '7 days' AND published_at < created_at - interval '30 days'"),
    ("disclosure", "duplicate_receipt", "error", "같은 접수번호(source_url) 중복",
     "SELECT count(*) FROM (SELECT 1 FROM disclosure WHERE source_url IS NOT NULL GROUP BY source_url HAVING count(*) > 1) t"),
    ("disclosure", "receipt_missing", "warning", "접수번호(source_url) 없음",
     "SELECT count(*) FROM disclosure WHERE source_url IS NULL OR source_url NOT LIKE '%rcptNo=%'"),
    ("disclosure", "disclosed_at_missing", "warning", "접수일 없음", "SELECT count(*) FROM disclosure WHERE disclosed_at IS NULL"),
    ("disclosure", "future_disclosed", "error", "지금보다 뒤의 접수일",
     "SELECT count(*) FROM disclosure WHERE disclosed_at > :now + interval '1 day'"),
    ("disclosure", "receipt_date_mismatch", "warning", "접수번호 앞 8자리(접수일)와 저장한 접수일이 다름",
     "SELECT count(*) FROM disclosure WHERE source_url LIKE '%rcptNo=%' AND disclosed_at IS NOT NULL "
     "AND substring(source_url from 'rcptNo=([0-9]{8})') <> to_char(disclosed_at AT TIME ZONE 'Asia/Seoul', 'YYYYMMDD')"),
    ("embeddings", "missing_recent", "warning", "최근 7일 저장된 뉴스·공시 중 임베딩 없음",
     "SELECT (SELECT count(*) FROM news WHERE embedding IS NULL AND created_at >= :now - interval '7 days') + "
     "(SELECT count(*) FROM disclosure WHERE embedding IS NULL AND created_at >= :now - interval '7 days')"),
    ("disclosure_content", "missing_recent", "warning", "최근 7일 저장된 공시 중 본문 없음",
     "SELECT count(*) FROM disclosure WHERE content IS NULL AND created_at >= :now - interval '7 days'"),
    # ETF: app/look_through._quality와 같은 기준(비중 100 초과 또는 양의 비중 합계 0 이하 = 분해 불가, 합계 > 100.5·< 95 = 경고)
    ("etf_holdings", "weight_over_100", "error", "마지막 기준일 구성 비중 100% 초과(look-through가 분해하지 않음)",
     "SELECT count(*) FROM etf_holding h JOIN (SELECT company_id, max(as_of_date) d FROM etf_holding GROUP BY company_id) l "
     "ON l.company_id = h.company_id AND l.d = h.as_of_date WHERE h.weight_pct > 100"),
    ("etf_holdings", "nonpositive_weight_sum", "error", "마지막 기준일 양의 비중 합계 0 이하",
     "SELECT count(*) FROM (SELECT h.company_id, sum(CASE WHEN h.weight_pct > 0 THEN h.weight_pct ELSE 0 END) s FROM etf_holding h "
     "JOIN (SELECT company_id, max(as_of_date) d FROM etf_holding GROUP BY company_id) l ON l.company_id = h.company_id AND l.d = h.as_of_date "
     "GROUP BY h.company_id) t WHERE s <= 0"),
    ("etf_holdings", "negative_weight", "warning", "음수 비중(look-through가 빼고 계산)",
     "SELECT count(*) FROM etf_holding WHERE weight_pct < 0"),
    ("etf_holdings", "weight_sum_out_of_range", "warning", "마지막 기준일 양의 비중 합계가 95% 미만 또는 100.5% 초과",
     "SELECT count(*) FROM (SELECT h.company_id, sum(CASE WHEN h.weight_pct > 0 THEN h.weight_pct ELSE 0 END) s FROM etf_holding h "
     "JOIN (SELECT company_id, max(as_of_date) d FROM etf_holding GROUP BY company_id) l ON l.company_id = h.company_id AND l.d = h.as_of_date "
     "GROUP BY h.company_id) t WHERE s > 100.5 OR s < 95"),
    ("etf_holdings", "duplicate_code", "warning", "같은 ETF·기준일에 같은 종목코드가 두 번(이름만 다름)",
     "SELECT count(*) FROM (SELECT 1 FROM etf_holding WHERE holding_code IS NOT NULL AND holding_code <> '' "
     "GROUP BY company_id, as_of_date, holding_code HAVING count(*) > 1) t"),
    ("etf_holdings", "non_etf_company", "error", "ETF가 아닌 종목에 구성종목이 붙음",
     "SELECT count(*) FROM (SELECT DISTINCT h.company_id FROM etf_holding h JOIN company c ON c.id = h.company_id "
     "WHERE c.asset_type <> 'etf') t"),
    ("etf_holdings", "stale_etf", "warning", "마지막 구성 기준일이 2영업일 이상 늦은 ETF",
     "SELECT count(*) FROM (SELECT company_id, max(as_of_date) d FROM etf_holding GROUP BY company_id) t WHERE d < :stale_before"),
    ("etf_holdings", "supported_etf_without_data", "warning", "KODEX·TIGER인데 구성종목이 없는 ETF",
     "SELECT count(*) FROM company c WHERE c.asset_type = 'etf' AND (c.name LIKE 'KODEX%' OR c.name LIKE 'TIGER%') "
     "AND NOT EXISTS (SELECT 1 FROM etf_holding h WHERE h.company_id = c.id)"),
)


QUALITY_ALIAS = {"etf_price": "stock_price"}


def _stale_before(today: date) -> date:
    """etf 기준일이 이 날보다 이르면 stale(look-through와 같은 is_stale: 최근 평일 대비 2영업일 이상)."""
    d = last_business_day(today)
    n = 0
    while n < STALE_BUSINESS_DAYS - 1:
        d -= timedelta(days=1)
        if d.weekday() < 5:
            n += 1
    return d


def quality_status(checks: list[dict]) -> str:
    if any(c["severity"] == "error" and c["count"] for c in checks):
        return "error"
    if any(c["severity"] == "warning" and c["count"] for c in checks):
        return "warning"
    return "ok"


def check_quality(db, now: datetime, sources: list[str] | None = None) -> dict[str, dict]:
    """품질 검사. 검사가 실패하면(SQL 오류) 그 검사는 count=None·severity=error로 남긴다(검사를 못 했으면 통과가 아니다)."""
    now = now.astimezone(KST)
    params = {"today": now.date(), "now": now, "since": now.date() - timedelta(days=60), "stale_before": _stale_before(now.date())}
    out: dict[str, dict] = {}
    for src, code, sev, label, sql in QUALITY_CHECKS:
        if sources and src not in sources:
            continue
        try:
            n = int(db.execute(text(sql), params).scalar() or 0)
            item = {"code": code, "severity": sev, "label": label, "count": n}
        except Exception as e:  # noqa: BLE001
            db.rollback()
            item = {"code": code, "severity": "error", "label": f"{label}(검사 실패: {type(e).__name__})", "count": None}
        out.setdefault(src, {"checks": []})["checks"].append(item)
    for v in out.values():
        v["status"] = quality_status([c if c["count"] is not None else {**c, "count": 1} for c in v["checks"]])
    return out


# ---------- 보고서 ----------


def build_report(db, now: datetime | None = None, history_fn=None, running_steps: set[str] | None = None,
                 last_runs: list[dict] | None = None) -> dict:
    """전체 상태 보고서(JSON으로 그대로 저장 가능). history_fn(db, steps) -> {step: history}."""
    from app import refresh_runs

    now = (now or datetime.now(KST)).astimezone(KST)
    facts = collect_facts(db)
    quality = check_quality(db, now)
    steps = sorted({s.step for s in SOURCES if s.step})
    hist = (history_fn or refresh_runs.step_history)(db, steps)
    # ETF 종가는 주가와 같은 표(stock_price)라 같은 품질 검사 결과를 쓴다
    rows = [evaluate_source(s, facts.get(s.key, SourceFacts()), hist.get(s.step) if s.step else None,
                            quality.get(s.key) or quality.get(QUALITY_ALIAS.get(s.key, "")), now, running_steps) for s in SOURCES]
    return {
        "generated_at": now.isoformat(timespec="seconds"),
        "timezone": "Asia/Seoul",
        "sources": rows,
        "summary": summarize(rows),
        "recent_runs": [{k: _iso(v) if isinstance(v, datetime) else v for k, v in r.items()} for r in (last_runs or [])],
        "notes": ["영업일은 평일 기준이다(KRX 공휴일 달력 없음). 지연 2영업일 이상을 stale로 본다.",
                  "usable_for_analysis는 데이터 기준일과 품질로만 정한다(마지막 실행 실패와 별개).",
                  "records_updated·records_skipped는 기존 수집기가 세지 않아 null이다."],
    }


def summarize(rows: list[dict]) -> dict:
    enabled = [r for r in rows if r["status"] not in ("unsupported", "disabled")]
    by = {s: [r["source"] for r in rows if r["status"] == s] for s in STATUSES}
    return {
        "overall": ("failed" if by["failed"] else "partial" if by["partial"] else "stale" if by["stale"] else
                    "running" if by["running"] else "never_run" if by["never_run"] else "healthy"),
        "by_status": {k: v for k, v in by.items() if v},
        "not_usable": [r["source"] for r in enabled if not r["usable_for_analysis"]],
        "quality_errors": [r["source"] for r in rows if r["quality_status"] == "error"],
        "quality_warnings": [r["source"] for r in rows if r["quality_status"] == "warning"],
    }


def status_exit_code(report: dict) -> int:
    """--status 종료 코드: 0 = 사용 중인 소스가 전부 healthy·분석 가능, 3 = 하나라도 아님(경고)."""
    s = report["summary"]
    return 0 if s["overall"] in ("healthy", "running") and not s["not_usable"] and not s["quality_errors"] else 3


def _cell(v) -> str:
    return "-" if v is None or v == "" else str(v)


def render_table(report: dict) -> str:
    head = f"{'source':<26}{'status':<12}{'usable':<8}{'data_as_of':<12}{'expected':<12}{'lag':>4}  {'quality':<8}last_success"
    lines = [head, "-" * len(head)]
    for r in report["sources"]:
        lines.append(f"{r['source']:<26}{r['status']:<12}{('yes' if r['usable_for_analysis'] else 'no'):<8}"
                     f"{_cell(r['data_as_of']):<12}{_cell(r['expected_as_of']):<12}{_cell(r['lag_business_days']):>4}  "
                     f"{_cell(r['quality_status']):<8}{_cell(r['last_success_at'])}")
    return "\n".join(lines)


def render_markdown(report: dict) -> str:
    s = report["summary"]
    L = [f"# 데이터 갱신 상태 보고서", "", f"- 생성: {report['generated_at']} (KST)", f"- 전체: **{s['overall']}**",
         f"- 분석에 쓸 수 없는 소스: {', '.join(s['not_usable']) or '없음'}",
         f"- 품질 error: {', '.join(s['quality_errors']) or '없음'} · 품질 warning: {', '.join(s['quality_warnings']) or '없음'}", "",
         "| 소스 | 상태 | 분석 가능 | 데이터 기준일 | 기대 기준일 | 지연(영업일) | 품질 | 마지막 성공 | 마지막 실패 | 사유 |",
         "|---|---|---|---|---|---|---|---|---|---|"]
    for r in report["sources"]:
        L.append(f"| {r['label']} (`{r['source']}`) | {r['status']} | {'예' if r['usable_for_analysis'] else '아니오'} | "
                 f"{_cell(r['data_as_of'])} | {_cell(r['expected_as_of'])} | {_cell(r['lag_business_days'])} | {_cell(r['quality_status'])} | "
                 f"{_cell(r['last_success_at'])} | {_cell(r['last_failure_at'])}"
                 f"{' (' + r['last_failure_reason'] + ')' if r['last_failure_reason'] else ''} | {'; '.join(r['reasons']) or '-'} |")
    issues = [(r["source"], c) for r in report["sources"] for c in r["quality_checks"] if c["count"] or c["count"] is None]
    L += ["", "## 품질 검사에서 걸린 항목", ""]
    L += [f"- `{src}` {c['severity']} `{c['code']}` {c['label']}: {_cell(c['count'])}건" for src, c in issues] or ["- 없음"]
    if report.get("recent_runs"):
        L += ["", "## 최근 실행", "", "| run | 시작 | 끝 | 상태 | 단계 | 종료 코드 | 비고 |", "|---|---|---|---|---|---|---|"]
        for r in report["recent_runs"]:
            L.append(f"| {r['id']} | {_cell(r.get('started_at'))} | {_cell(r.get('finished_at'))} | {r['status']} | {r['steps']} | "
                     f"{_cell(r.get('exit_code'))} | {_cell(r.get('note'))} |")
    L += ["", "## 참고", ""] + [f"- {n}" for n in report["notes"]]
    return "\n".join(L) + "\n"


def write_reports(report: dict, directory, stem: str = "refresh_status") -> tuple[str, str]:
    from pathlib import Path

    d = Path(directory)
    d.mkdir(parents=True, exist_ok=True)
    jp, mp = d / f"{stem}.json", d / f"{stem}.md"
    jp.write_text(json.dumps(report, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    mp.write_text(render_markdown(report), encoding="utf-8")
    return str(jp), str(mp)


# ---------- 알림(인터페이스만, 외부 연결 없음) ----------


class Notifier(Protocol):
    def notify(self, event: dict) -> None: ...


class LogNotifier:
    """기본 알림: 표준 오류와 logging으로만 낸다. Slack·이메일은 이 인터페이스를 구현해 붙인다(토큰은 코드에 두지 않는다)."""

    def __init__(self, stream=None):
        self.stream = stream or sys.stderr

    def notify(self, event: dict) -> None:
        line = f"[ALERT:{event['level']}] {event['source']}: {event['message']}"
        print(line, file=self.stream)
        logger.warning(line)


def alerts(report: dict) -> list[dict]:
    """보고서에서 알릴 일: 실패·부분 성공(error), 지연·분석 불가·품질 error(warning)."""
    out = []
    for r in report["sources"]:
        if r["status"] in ("unsupported", "disabled"):
            continue
        if r["status"] in ("failed", "partial"):
            out.append({"level": "error", "source": r["source"], "message": f"{r['status']}: {'; '.join(r['reasons'])}"})
        elif r["status"] == "stale":
            out.append({"level": "warning", "source": r["source"], "message": "; ".join(r["reasons"])})
        if r["quality_status"] == "error":
            bad = [c["code"] for c in r["quality_checks"] if c["severity"] == "error" and (c["count"] or c["count"] is None)]
            out.append({"level": "error", "source": r["source"], "message": f"품질 error: {', '.join(bad)}"})
    return out


def dispatch(report: dict, notifiers: list[Notifier] | None = None) -> list[dict]:
    evs = alerts(report)
    for n in notifiers if notifiers is not None else [LogNotifier()]:
        for ev in evs:
            try:
                n.notify(ev)
            except Exception as e:  # noqa: BLE001 - 알림 실패가 수집·보고를 막지 않는다
                logger.warning("알림 실패 %s: %s", type(n).__name__, type(e).__name__)
    return evs


def step_quality(report: dict) -> dict[str, tuple[str, int]]:
    """단계별 품질(그 단계가 만드는 소스 중 가장 나쁜 값)과 경고 수. data_refresh_log.quality_status에 쓴다."""
    rank = {"ok": 0, "warning": 1, "error": 2}
    out: dict[str, tuple[str, int]] = {}
    for r in report["sources"]:
        if not r["step"] or r["quality_status"] is None:
            continue
        prev = out.get(r["step"], ("ok", 0))
        q = r["quality_status"] if rank[r["quality_status"]] > rank[prev[0]] else prev[0]
        out[r["step"]] = (q, prev[1] + (r["warning_count"] or 0))
    return out


def now_kst(clock: Callable[[], datetime] | None = None) -> datetime:
    return (clock or (lambda: datetime.now(KST)))().astimezone(KST)
