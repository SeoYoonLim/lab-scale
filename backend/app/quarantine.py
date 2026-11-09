"""수집 데이터 격리 기록·조회·재검증·재처리(2026-11-08, 36차). 표는 data_ingestion_quarantine(app/models), 판정은 app/ingest_validation.

- record(): 수집기가 저장 직전에 부른다. 격리(quarantined)와 경고(warning) 레코드를 남긴다. 같은 데이터가 다시 걸리면 새 행 대신
  occurrences를 올린다. 기록에 실패해도 수집은 계속한다(기록 실패는 로그로만, 격리 대상은 여전히 저장하지 않는다).
- 실행 맥락: scripts/refresh_data가 단계마다 set_context(run_id, step)를 부르면 기록에 run_id·step이 붙고, 단계가 끝난 뒤
  counts_for_run()으로 격리 건수를 세어 단계 결과를 부분 성공(quality_quarantined)으로 표시한다.
- 재처리(reprocess)는 같은 키의 정상 데이터가 이미 있으면 넣지 않는다(kept_existing). 기존 정상 데이터를 덮어쓰지 않는다.
- 일반 사용자 API는 없다(관리자 역할 체계가 없다). 운영 CLI: python -m scripts.quarantine.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Callable

from sqlalchemy import delete, func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app import ingest_validation as V

logger = logging.getLogger(__name__)

MAX_PAYLOAD_BYTES = 65536  # ETF 구성종목(수백 종목)도 들어가는 크기. 넘으면 잘라 저장하고 재처리를 막는다(다시 수집해야 함)
MAX_TEXT = {"title": 300, "url": 500, "content": 500, "report_nm": 300, "name": 200}
SOURCES = ("stock_price", "market_index", "exchange_rate", "news", "disclosure", "etf_holding")
OPEN_STATES = ("open", "revalidated", "on_hold")
PRUNE_MIN_DAYS = 30

_CONTEXT: dict = {"run_id": None, "step": None}


def set_context(run_id: int | None, step: str | None) -> None:
    _CONTEXT.update(run_id=run_id, step=step)


def clear_context() -> None:
    set_context(None, None)


def current_context() -> dict:
    return dict(_CONTEXT)


# ---------- payload(재검증에 필요한 최소 필드만) ----------

_FIELDS = {
    "stock_price": ("ticker", "price_date", "open_price", "high_price", "low_price", "close_price", "volume", "change_pct"),
    "market_index": ("index_code", "price_date", "close_price", "change_pct"),
    "exchange_rate": ("pair_code", "price_date", "close_price", "change_pct"),
    "news": ("ticker", "title", "url", "source", "published_at", "content"),
    "disclosure": ("ticker", "rcept_no", "rcept_dt", "report_nm", "corp_name"),
    "etf_holding": ("ticker", "name", "as_of", "source", "holdings"),
}


def _plain(v):
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    if isinstance(v, float) and v != v:
        return None
    return v


def sanitize(source: str, record: dict) -> tuple[dict, bool]:
    """(payload, 잘렸는지). 정해진 필드만 남기고 긴 글은 자른다. API 키·사용자 데이터는 애초에 필드에 없다."""
    out = {}
    for k in _FIELDS[source]:
        v = _plain(record.get(k))
        if isinstance(v, str) and k in MAX_TEXT:
            v = v[:MAX_TEXT[k]]
        if k == "holdings" and v is not None:
            v = [[str(h.get("name") or "")[:MAX_TEXT["name"]], h.get("code"), _plain(h.get("weight_pct"))] for h in v]
        out[k] = v
    raw = json.dumps(out, ensure_ascii=False, sort_keys=True)
    if len(raw.encode("utf-8")) <= MAX_PAYLOAD_BYTES:
        return out, False
    if source == "etf_holding":  # 앞에서부터 들어가는 만큼만 남긴다(재처리 불가 표시)
        keep = out["holdings"]
        while keep and len(json.dumps({**out, "holdings": keep}, ensure_ascii=False).encode("utf-8")) > MAX_PAYLOAD_BYTES:
            keep = keep[: len(keep) * 3 // 4]
        return {**out, "holdings": keep}, True
    return {k: (v[:200] if isinstance(v, str) else v) for k, v in out.items()}, True


def payload_hash(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def _as_of(source: str, payload: dict) -> date | None:
    v = payload.get({"news": "published_at", "disclosure": "rcept_dt", "etf_holding": "as_of"}.get(source, "price_date"))
    if not v:
        return None
    try:
        return datetime.strptime(v, "%Y%m%d").date() if source == "disclosure" else date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


# ---------- 기록 ----------


def record(source: str, checked: list[V.Checked], extra: dict | None = None,
           session_factory: Callable | None = None) -> dict:
    """격리·경고 레코드를 남긴다. extra는 payload에 더할 공통 필드(예: ticker). 반환: {"quarantined", "warning", "record_failed"}.
    별도 세션에서 커밋한다(수집 트랜잭션과 따로: 수집이 실패해도 격리 기록은 남고, 기록이 실패해도 수집은 계속한다)."""
    rows = [c for c in checked if c.status != V.VALID]
    stats = {"quarantined": sum(c.status == V.QUARANTINED for c in rows), "warning": sum(c.status == V.WARNING for c in rows),
             "record_failed": 0}
    if not rows:
        return stats
    if session_factory is None:
        from app.db.session import SessionLocal as session_factory
    from app.models import DataIngestionQuarantine as Q

    ctx = current_context()
    db = session_factory()
    try:
        for c in rows:
            payload, truncated = sanitize(source, {**(extra or {}), **c.record})
            first = c.verdict.issues[0]
            detail = "; ".join(f"{i.code}: {i.detail}" for i in c.verdict.issues)[:500]
            stmt = pg_insert(Q).values(
                source=source, source_key=c.key[:300], data_as_of=_as_of(source, payload), validation_status=c.status,
                error_code=first.code[:40], error_detail=detail, payload=payload, payload_hash=payload_hash(payload),
                payload_truncated=int(truncated), run_id=ctx["run_id"], step=ctx["step"])
            stmt = stmt.on_conflict_do_update(
                constraint="uq_ingest_q_identity",
                set_={"occurrences": Q.occurrences + 1, "last_seen_at": func.now(), "run_id": stmt.excluded.run_id,
                      "step": stmt.excluded.step, "error_detail": stmt.excluded.error_detail})
            db.execute(stmt)
        db.commit()
    except Exception as e:  # noqa: BLE001 - 기록 실패가 수집을 막지 않는다(격리 대상은 이미 저장 목록에서 빠졌다)
        db.rollback()
        stats["record_failed"] = len(rows)
        logger.warning("격리 기록 실패(%s, %d건): %s", source, len(rows), type(e).__name__)
        print(f"[경고] 격리 기록 실패({source} {len(rows)}건): {type(e).__name__}")
    finally:
        db.close()
    return stats


def counts_for_run(db, run_id: int, step: str | None = None) -> dict:
    """이 실행(단계)에서 새로 걸리거나 다시 걸린 격리·경고 건수."""
    sql = ("SELECT validation_status, count(*) FROM data_ingestion_quarantine WHERE run_id = :r"
           + (" AND step = :s" if step else "") + " GROUP BY 1")
    got = dict(db.execute(text(sql), {"r": run_id, "s": step}).all())
    return {"quarantined": got.get("quarantined", 0), "warning": got.get("warning", 0)}


def open_summary(db) -> dict[str, dict]:
    """소스별 미해결 격리 건수와 오류 코드별 건수(운영 보고서용)."""
    rows = db.execute(text(
        "SELECT source, error_code, count(*), max(last_seen_at) FROM data_ingestion_quarantine "
        "WHERE validation_status = 'quarantined' AND resolution_status IN ('open', 'revalidated', 'on_hold') GROUP BY 1, 2")).all()
    out: dict[str, dict] = {}
    for src, code, n, last in rows:
        s = out.setdefault(src, {"open_quarantined": 0, "by_code": {}, "last_seen_at": None})
        s["open_quarantined"] += n
        s["by_code"][code] = n
        s["last_seen_at"] = max(filter(None, [s["last_seen_at"], last.isoformat() if last else None]), default=None)
    return out


# ---------- 조회·상태 변경 ----------


def list_items(db, source: str | None = None, code: str | None = None, status: str | None = None,
               validation: str | None = "quarantined", limit: int = 50) -> list:
    from app.models import DataIngestionQuarantine as Q

    q = select(Q).order_by(Q.last_seen_at.desc(), Q.id.desc()).limit(limit)
    if source:
        q = q.where(Q.source == source)
    if code:
        q = q.where(Q.error_code == code)
    if status:
        q = q.where(Q.resolution_status == status)
    if validation:
        q = q.where(Q.validation_status == validation)
    return list(db.execute(q).scalars())


def get(db, item_id: int):
    from app.models import DataIngestionQuarantine as Q

    return db.get(Q, item_id)


def _resolve(db, item, status: str, note: str | None = None, now: datetime | None = None) -> None:
    now = now or datetime.now(timezone.utc)
    item.resolution_status = status
    item.reviewed_at = now
    if status in ("reprocessed", "kept_existing", "discarded"):
        item.resolved_at = now
    if note:
        item.resolution_note = note[:300]
    db.commit()


def hold(db, item_id: int, note: str | None = None) -> str:
    item = get(db, item_id)
    if item is None:
        return "not_found"
    _resolve(db, item, "on_hold", note)
    return "on_hold"


def discard(db, item_id: int, note: str | None = None) -> str:
    item = get(db, item_id)
    if item is None:
        return "not_found"
    _resolve(db, item, "discarded", note)
    return "discarded"


# ---------- 재검증 ----------


def _company(db, ticker: str | None):
    from app.models import Company

    return db.execute(select(Company).where(Company.ticker == ticker)).scalar_one_or_none() if ticker else None


def _series_context(db, source: str, p: dict) -> tuple[dict, float | None]:
    """재검증할 시세 한 건의 (같은 날짜 기존 값, 직전 저장 값)."""
    from app.models import ExchangeRate, MarketIndex, StockPrice

    d = date.fromisoformat(p["price_date"])
    if source == "stock_price":
        co = _company(db, p.get("ticker"))
        if co is None:
            return {}, None
        model, cond = StockPrice, StockPrice.company_id == co.id
    elif source == "market_index":
        model, cond = MarketIndex, MarketIndex.index_code == p.get("index_code")
    else:
        model, cond = ExchangeRate, ExchangeRate.pair_code == p.get("pair_code")
    same = db.execute(select(model.close_price).where(cond, model.price_date == d)).scalar()
    prev = db.execute(select(model.close_price).where(cond, model.price_date < d).order_by(model.price_date.desc()).limit(1)).scalar()
    return ({d: float(same)} if same is not None else {}), (float(prev) if prev is not None else None)


def validate_payload(db, source: str, p: dict, today: date | None = None, now: datetime | None = None) -> V.Verdict:
    """저장된 payload를 지금의 규칙·지금의 DB 상태로 다시 판정한다."""
    if source in ("stock_price", "market_index", "exchange_rate"):
        rec = {**p, "price_date": date.fromisoformat(p["price_date"]) if p.get("price_date") else None}
        existing, prev = _series_context(db, source, p) if rec["price_date"] else ({}, None)
        fn = {"stock_price": lambda: V.check_prices(p.get("ticker") or "", [rec], existing, prev, today),
              "market_index": lambda: V.check_index(p.get("index_code") or "", [rec], existing, prev, today),
              "exchange_rate": lambda: V.check_fx(p.get("pair_code") or "", [rec], existing, prev, today)}[source]
        return fn()[0].verdict
    if source == "news":
        pub = p.get("published_at")
        try:
            pub = datetime.fromisoformat(pub) if pub else None
        except ValueError:
            pass
        v = V.check_news(p.get("ticker"), [{**p, "published_at": pub}], now)[0].verdict
        if v.status != V.QUARANTINED and _company(db, p.get("ticker")) is None:
            v.reject("company_missing", f"종목 {p.get('ticker')}이 company에 없음")
        return v
    if source == "disclosure":
        v = V.check_disclosures(p.get("ticker"), [p], now)[0].verdict
        if v.status != V.QUARANTINED and _company(db, p.get("ticker")) is None:
            v.reject("company_missing", f"종목 {p.get('ticker')}이 company에 없음")
        return v
    if source == "etf_holding":
        from app.etf_holdings import expected_total

        holdings = [{"name": h[0], "code": h[1], "weight_pct": h[2]} for h in p.get("holdings") or []]
        as_of = date.fromisoformat(p["as_of"]) if p.get("as_of") else None
        return V.check_etf_snapshot(p.get("ticker") or "", p.get("name") or "", as_of, holdings, expected_total(p.get("name") or ""), today)
    raise ValueError(f"알 수 없는 source {source}")


def revalidate(db, item_id: int, today: date | None = None, now: datetime | None = None) -> str:
    """다시 판정해 통과(valid·warning)하면 revalidated(재처리 대기), 아니면 open 그대로 두고 사유를 새로 쓴다."""
    item = get(db, item_id)
    if item is None:
        return "not_found"
    if item.validation_status != "quarantined" or item.resolution_status in ("reprocessed", "kept_existing", "discarded"):
        return f"skipped:{item.resolution_status}"
    v = validate_payload(db, item.source, item.payload, today, now)
    item.retry_count += 1
    item.reviewed_at = datetime.now(timezone.utc)
    if v.status == V.QUARANTINED:
        item.error_detail = "; ".join(f"{i.code}: {i.detail}" for i in v.issues)[:500]
        db.commit()
        return "still_quarantined"
    item.resolution_status = "revalidated"
    item.resolution_note = ("재검증 통과" + (f"(경고: {', '.join(v.codes)})" if v.issues else ""))[:300]
    db.commit()
    return "revalidated"


# ---------- 재처리(기존 정상 데이터를 덮어쓰지 않음) ----------


def _exists(db, source: str, p: dict) -> bool:
    from app.models import Disclosure, EtfHolding, ExchangeRate, MarketIndex, News, StockPrice

    if source == "stock_price":
        co = _company(db, p.get("ticker"))
        return co is not None and db.execute(select(StockPrice.id).where(
            StockPrice.company_id == co.id, StockPrice.price_date == date.fromisoformat(p["price_date"]))).first() is not None
    if source == "market_index":
        return db.execute(select(MarketIndex.id).where(MarketIndex.index_code == p["index_code"],
                                                       MarketIndex.price_date == date.fromisoformat(p["price_date"]))).first() is not None
    if source == "exchange_rate":
        return db.execute(select(ExchangeRate.id).where(ExchangeRate.pair_code == p["pair_code"],
                                                        ExchangeRate.price_date == date.fromisoformat(p["price_date"]))).first() is not None
    if source == "news":
        co = _company(db, p.get("ticker"))
        return co is not None and db.execute(select(News.id).where(News.url == p["url"], News.company_id == co.id)).first() is not None
    if source == "disclosure":
        return db.execute(select(Disclosure.id).where(Disclosure.source_url == _dart_url(p["rcept_no"]))).first() is not None
    if source == "etf_holding":
        co = _company(db, p.get("ticker"))
        return co is not None and db.execute(select(EtfHolding.id).where(
            EtfHolding.company_id == co.id, EtfHolding.as_of_date == date.fromisoformat(p["as_of"]))).first() is not None
    raise ValueError(source)


def _dart_url(rcept_no: str) -> str:
    return f"https://dart.fss.or.kr/dsaf001/main.do?rcptNo={rcept_no}"


def _insert(db, source: str, p: dict) -> None:
    from app.models import Disclosure, EtfHolding, ExchangeRate, MarketIndex, News, StockPrice
    from app.ingest_validation import KST

    if source == "stock_price":
        co = _company(db, p["ticker"])
        db.add(StockPrice(company_id=co.id, price_date=date.fromisoformat(p["price_date"]),
                          **{k: p.get(k) for k in ("open_price", "high_price", "low_price", "close_price", "volume", "change_pct")}))
    elif source == "market_index":
        db.add(MarketIndex(index_code=p["index_code"], price_date=date.fromisoformat(p["price_date"]),
                           close_price=p["close_price"], change_pct=p.get("change_pct")))
    elif source == "exchange_rate":
        db.add(ExchangeRate(pair_code=p["pair_code"], price_date=date.fromisoformat(p["price_date"]),
                            close_price=p["close_price"], change_pct=p.get("change_pct")))
    elif source == "news":
        from app.news_relevance import is_relevant_news
        from app.models import Company

        co = _company(db, p["ticker"])
        listed = [n for (n,) in db.execute(select(Company.name))]
        db.add(News(company_id=co.id, title=p["title"], content=p.get("content"), source=p.get("source"), url=p["url"],
                    published_at=datetime.fromisoformat(p["published_at"]),
                    is_relevant=is_relevant_news(co.name, co.ticker, co.asset_type, p["title"], p.get("content") or "", listed)))
    elif source == "disclosure":
        co = _company(db, p["ticker"])
        db.add(Disclosure(company_id=co.id, title=p["report_nm"].strip(), disclosure_type=p["report_nm"].strip()[:50], content=None,
                          source_url=_dart_url(p["rcept_no"]),
                          disclosed_at=datetime.strptime(p["rcept_dt"], "%Y%m%d").replace(tzinfo=KST)))
    elif source == "etf_holding":
        co = _company(db, p["ticker"])
        now = datetime.now(timezone.utc)
        db.add_all(EtfHolding(company_id=co.id, as_of_date=date.fromisoformat(p["as_of"]), holding_name=h[0][:200],
                              holding_code=(h[1] or None) and str(h[1])[:30], weight_pct=h[2], source=p.get("source"), collected_at=now)
                   for h in p["holdings"])


def reprocess(db, item_id: int, apply: bool = False) -> str:
    """revalidated 상태만 업무 테이블에 넣는다. 같은 키의 데이터가 이미 있으면 넣지 않고 kept_existing(기존 정상 데이터 보호).
    apply=False면 무엇을 할지만 돌려준다."""
    item = get(db, item_id)
    if item is None:
        return "not_found"
    if item.resolution_status != "revalidated":
        return f"skipped:{item.resolution_status}(먼저 revalidate)"
    if item.payload_truncated:
        return "skipped:payload_truncated(다시 수집해야 함)"
    p = item.payload
    if item.source in ("stock_price", "news", "disclosure", "etf_holding") and _company(db, p.get("ticker")) is None:
        return "skipped:company_missing"
    if _exists(db, item.source, p):
        if apply:
            _resolve(db, item, "kept_existing", "같은 키의 데이터가 이미 있어 넣지 않음(기존 정상 데이터 유지)")
        return "kept_existing"
    if not apply:
        return "would_insert"
    try:
        _insert(db, item.source, p)
        db.flush()
    except Exception as e:  # noqa: BLE001
        db.rollback()
        return f"failed:{type(e).__name__}"
    _resolve(db, item, "reprocessed", "재검증 뒤 업무 테이블에 넣음")
    return "reprocessed"


# ---------- 정리 ----------


def prune(db, keep_days: int = 180, dry_run: bool = False, include_open: bool = False, now: datetime | None = None,
          key_prefix: str | None = None) -> int:
    """마지막으로 본 지 keep_days일 넘은 기록을 지운다. 기본은 끝난 것(경고·reprocessed·kept_existing·discarded)만이고,
    미해결 격리(open·revalidated·on_hold)는 include_open일 때만 지운다(사람이 보기 전에 사라지지 않게)."""
    from app.models import DataIngestionQuarantine as Q

    if keep_days < PRUNE_MIN_DAYS:
        raise ValueError(f"keep_days는 {PRUNE_MIN_DAYS} 이상이어야 합니다")
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=keep_days)
    cond = [Q.last_seen_at < cutoff]
    if key_prefix:  # 특정 키만(테스트·부분 정리)
        cond.append(Q.source_key.startswith(key_prefix))
    if not include_open:
        cond.append((Q.validation_status == "warning") | Q.resolution_status.in_(("reprocessed", "kept_existing", "discarded")))
    n = db.execute(select(func.count()).select_from(Q).where(*cond)).scalar()
    if not dry_run and n:
        db.execute(delete(Q).where(*cond))
        db.commit()
    return n
