"""61차(2026-11-15): 뉴스·공시 최신성과 수집 기록의 의미(docs/NEWS_DISCLOSURE_FRESHNESS_AUDIT_61.md).

- 순수 테스트: 게시·접수 시각과 수집(행 생성) 시각을 섞지 않는다, UTC -> KST 날짜, 빈 데이터, 수집 실패와 지연의 구분,
  "게시는 최근인데 수집이 오래됨"과 "수집은 최근인데 이 종목의 새 항목이 없음", 마지막 refresh_data 실행 요약의 실제 의미.
- integration(격리 DB): 수집기의 중복 저장 방지(같은 원천 응답을 두 번 넣어도 0건), 개별 수집은 data_refresh_log를 남기지 않는다
  (= GET /api/status의 last_refresh는 refresh_data 실행만 뜻한다), 종목별 최신(company_latest_at)과 표 전체의 마지막 저장(last_collected_at).
네이버·DART 요청은 가짜 함수로 바꾼다(외부 요청 없음).
"""
import secrets
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, text

from app import collectors, data_status as DS, stock_feed as sf

KST = ZoneInfo("Asia/Seoul")
UTC = timezone.utc


def kst(*a):
    return datetime(*a, tzinfo=KST)


# ---------- 1·2. 게시·접수 시각과 수집 시각 ----------

def test_news_item_keeps_published_and_collected_apart():
    row = SimpleNamespace(id=1, title="t", content="c", source="s", url="u", published_at=kst(2026, 10, 14, 9, 0),
                          created_at=kst(2026, 11, 3, 19, 54), is_relevant=True)
    it = sf.news_item(row, [])
    assert it["published_at"] == "2026-10-14T09:00:00+09:00" and it["collected_at"] == "2026-11-03T19:54:00+09:00"


def test_disclosure_item_keeps_receipt_date_and_collected_apart():
    row = SimpleNamespace(id=1, title="t", disclosure_type="t", source_url="https://dart.fss.or.kr/dsaf001/main.do?rcptNo=20261030000586",
                          disclosed_at=kst(2026, 10, 30), created_at=kst(2026, 10, 31, 12, 31))
    it = sf.disclosure_item(row)
    assert it["disclosed_date"] == date(2026, 10, 30) and it["disclosed_at"] == "2026-10-30T00:00:00+09:00"
    assert it["collected_at"] == "2026-10-31T12:31:00+09:00" and it["rcept_no"] == "20261030000586"


# ---------- 3. UTC -> KST ----------

def test_status_dates_are_kst_dates():
    assert DS._kst_date(datetime(2026, 11, 3, 15, 30, tzinfo=UTC)) == date(2026, 11, 4)  # KST 11-04 00:30
    assert DS._kst_date(datetime(2026, 11, 3, 14, 59, tzinfo=UTC)) == date(2026, 11, 3)
    assert DS._kst_date(datetime(2026, 11, 3, 15, 30)) == date(2026, 11, 4)  # tz 없는 값은 UTC로 본다
    assert DS._kst_date(None) is None


def test_collection_lag_uses_the_kst_collection_date():
    late_utc = datetime(2026, 11, 3, 16, 0, tzinfo=UTC)  # KST 11-04(수) 01:00 수집
    f = sf.freshness(late_utc, None, date(2026, 11, 4))
    assert f["collection_lag_weekdays"] == 0 and f["last_collected_at"] == "2026-11-04T01:00:00+09:00"


# ---------- 5. 빈 데이터 ----------

def test_empty_tables_are_stale_and_have_no_dates():
    f = sf.freshness(None, None, date(2026, 11, 15))
    assert f["stale"] is True and f["collection_lag_weekdays"] is None and f["company_latest_at"] is None
    assert DS.summarize_last_run([]) is None


# ---------- 8·9·10. 실패와 지연, 게시와 수집 ----------

OLD_RUN = {"step": "news", "status": "success", "ok": True, "finished_at": "2026-11-03T19:54:42+09:00", "error_code": None}
FAILED_RUN = {"step": "news", "status": "failed", "ok": False, "finished_at": "2026-11-14T20:10:00+09:00", "error_code": "network"}


def test_delay_and_failure_are_told_apart_by_last_collection():
    """2026-11-15 실제 상태: 마지막 수집 11-03 성공(그 뒤 실행 없음) = 지연. 마지막 시도가 failed면 수집 실패다. 둘 다 stale이다."""
    last = kst(2026, 11, 3, 19, 54)
    delayed = sf.freshness(last, kst(2026, 11, 3, 19, 48), date(2026, 11, 15), OLD_RUN)
    failed = sf.freshness(last, kst(2026, 11, 3, 19, 48), date(2026, 11, 15), FAILED_RUN)
    assert delayed["stale"] and failed["stale"] and delayed["collection_lag_weekdays"] == 8
    assert delayed["last_collection"]["ok"] is True and failed["last_collection"]["ok"] is False


def test_recent_publication_but_old_collection_is_stale():
    """종목 최신 게시가 마지막 수집 직전(11-03 19:48)이어도, 수집이 8평일 전이면 최신이 아니다."""
    f = sf.freshness(kst(2026, 11, 3, 19, 54), kst(2026, 11, 3, 19, 48), date(2026, 11, 15), OLD_RUN)
    assert f["stale"] is True and f["company_latest_at"] == "2026-11-03T19:48:00+09:00"


def test_recent_collection_but_old_publication_is_not_stale():
    """오늘 수집했는데 이 종목의 최신 기사가 10-14면: 수집은 최신(stale=false)이고 그 종목에 새 기사가 없는 것이다."""
    f = sf.freshness(kst(2026, 11, 16, 20, 30), kst(2026, 10, 14, 9, 0), date(2026, 11, 16), OLD_RUN)
    assert f["stale"] is False and f["collection_lag_weekdays"] == 0 and f["company_latest_at"].startswith("2026-10-14")


# ---------- 7. 마지막 갱신 기록의 실제 의미 ----------

def _log(id_, step, status, started, finished=None, run_id=None):
    return SimpleNamespace(id=id_, step=step, status=status, started_at=started, finished_at=finished or started, run_id=run_id)


def test_last_refresh_is_the_last_refresh_data_run_even_if_it_skipped_news():
    """개발 DB 2026-11-03 기록 그대로: 19:45 전체 실행 뒤 20:03에 환율만 다시 돌린 실행(나머지 skipped)이 있다.
    last_refresh는 그 마지막 실행(20:03, ok)이다. 뉴스의 실제 마지막 수집은 last_attempt(news)로 따로 본다(19:54)."""
    t = kst(2026, 11, 3, 20, 3, 27)
    rows = [_log(36, "news", "skipped", t), _log(35, "index", "skipped", t), _log(34, "fx", "success", kst(2026, 11, 3, 20, 3, 25), t),
            _log(33, "prices", "skipped", t), _log(32, "embed", "skipped", t), _log(31, "content", "skipped", t),
            _log(30, "news", "success", kst(2026, 11, 3, 19, 48, 22), kst(2026, 11, 3, 19, 54, 42))]
    s = DS.summarize_last_run(rows)
    assert s == {"finished_at": "2026-11-03T20:03:27+09:00", "ok": True, "failed_steps": []}


def test_last_attempt_skips_skipped_rows_and_reports_failure():
    class FakeDB:
        def __init__(self, row):
            self.row, self.sql = row, None

        def execute(self, stmt):
            self.sql = str(stmt)
            return SimpleNamespace(first=lambda: self.row)

    db = FakeDB(SimpleNamespace(status="failed", finished_at=kst(2026, 11, 14, 20, 10), error_code="network"))
    got = DS.last_attempt(db, "news")
    assert got == FAILED_RUN and "status !=" in db.sql and "data_refresh_log.step" in db.sql
    assert DS.last_attempt(FakeDB(None), "news") is None


# ---------- integration: 수집기 중복 방지, 개별 수집은 실행 기록이 없다, 종목별 최신 ----------

def _naver(link, title, pub="Thu, 05 Nov 2026 09:00:00 +0900"):
    return {"title": title, "description": "본문", "link": link, "originallink": link, "pubDate": pub}


@pytest.fixture
def made(dev_db):
    """가짜 종목(ticker Q + 16진 5자리 = KRX 6자리 형식). 끝나면 그 종목의 뉴스·공시·격리 기록·company를 지운다."""
    out = []

    def _make(name="captest_c61"):
        from app.models import Company

        ticker = ("Q" + secrets.token_hex(3)[:5]).upper()
        c = Company(ticker=ticker, name=f"{name} {ticker}", asset_type="stock")
        dev_db.add(c)
        dev_db.commit()
        out.append(c)
        return c

    yield _make
    dev_db.rollback()
    for c in out:
        dev_db.execute(text("DELETE FROM news WHERE company_id = :i"), {"i": c.id})
        dev_db.execute(text("DELETE FROM disclosure WHERE company_id = :i"), {"i": c.id})
        dev_db.execute(text("DELETE FROM data_ingestion_quarantine WHERE source_key LIKE :k OR payload->>'ticker' = :t"),
                       {"k": f"%:{c.ticker}:%", "t": c.ticker})
        dev_db.execute(text("DELETE FROM company WHERE id = :i"), {"i": c.id})
    dev_db.commit()


def _refresh_log_count(db) -> int:
    return db.execute(text("SELECT count(*) FROM data_refresh_log")).scalar_one()


@pytest.mark.integration
def test_news_collector_dedups_and_leaves_no_refresh_log(dev_db, made, monkeypatch):
    from app.models import News

    co = made()
    links = [f"https://www.mk.co.kr/news/c61/{secrets.token_hex(4)}" for _ in range(2)]
    items = [_naver(links[0], f"{co.name} 신공장"), _naver(links[1], f"{co.name} 실적", "Thu, 05 Nov 2026 10:00:00 +0900")]
    monkeypatch.setattr(collectors, "fetch_naver_news", lambda q, display=10, sort="date": items)
    before = _refresh_log_count(dev_db)
    first = collectors.fetch_and_save_news(co.name, display=10, ticker=co.ticker)
    again = collectors.fetch_and_save_news(co.name, display=10, ticker=co.ticker)  # 같은 원천 응답을 다시
    assert first.ok and first.saved == 2 and again.ok and again.saved == 0
    dev_db.expire_all()
    assert sorted(n.url for n in dev_db.query(News).filter(News.company_id == co.id)) == sorted(links)
    assert _refresh_log_count(dev_db) == before  # 개별 수집은 data_refresh_log를 남기지 않는다


@pytest.mark.integration
def test_disclosure_collector_dedups_by_receipt_and_leaves_no_refresh_log(dev_db, made, monkeypatch):
    from app.models import Disclosure

    co = made()
    serial = f"{secrets.randbelow(10**6):06d}"
    items = [{"rcept_no": "20261105" + serial, "rcept_dt": "20261105", "report_nm": "기업설명회(IR)개최(안내공시)",
              "corp_name": co.name, "flr_nm": co.name}]
    monkeypatch.setattr(collectors, "get_dart_corp_code", lambda t: "00000000")
    monkeypatch.setattr(collectors, "fetch_dart_disclosures", lambda corp, days=90: items)
    before = _refresh_log_count(dev_db)
    first, again = collectors.fetch_and_save_disclosures(co.name), collectors.fetch_and_save_disclosures(co.name)
    assert first.ok and first.saved == 1 and again.ok and again.saved == 0
    dev_db.expire_all()
    rows = dev_db.query(Disclosure).filter(Disclosure.company_id == co.id).all()
    assert len(rows) == 1 and rows[0].disclosed_at.astimezone(KST) == kst(2026, 11, 5)  # 접수일 = KST 자정
    assert rows[0].created_at is not None and rows[0].created_at != rows[0].disclosed_at  # 수집 시각은 따로
    assert _refresh_log_count(dev_db) == before


@pytest.mark.integration
def test_company_latest_and_table_last_collected_differ(dev_db, made):
    """종목별 최신(company_latest_at = 그 종목의 최신 게시)과 표 전체 마지막 저장(last_collected_at = news.created_at 최대)은 다르다."""
    from app.main import app

    a, b = made(), made()
    for co, pub in ((a, kst(2026, 10, 14, 9)), (b, kst(2026, 11, 3, 18))):
        dev_db.execute(text("INSERT INTO news (company_id, title, source, url, published_at, is_relevant) VALUES (:c, :t, 'x', :u, :p, true)"),
                       {"c": co.id, "t": f"{co.name} 기사", "u": f"https://example.test/c61/{secrets.token_hex(4)}", "p": pub})
    dev_db.commit()
    body = TestClient(app).get(f"/api/stocks/{a.ticker}/news").json()
    from app.models import News

    table_last = dev_db.query(func.max(News.created_at)).scalar()
    assert body["freshness"]["company_latest_at"] == "2026-10-14T09:00:00+09:00"
    assert body["freshness"]["last_collected_at"] == table_last.astimezone(KST).isoformat()
    assert datetime.fromisoformat(body["freshness"]["last_collected_at"]) > kst(2026, 10, 14, 9) + timedelta(days=1)
