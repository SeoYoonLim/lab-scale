"""GET /api/stocks/{ticker}/news, /disclosures(41차, 2026-11-10): 순수 함수, 라우터(mock), 개발 DB(integration).

integration은 이 파일이 만든 테스트 전용 종목(티커 Z41…)과 그 뉴스·공시 행만 쓰고 끝나면 지운다. 수집 데이터가 없는 CI 빈 DB에서도
그대로 돈다(needs_collected_data 마커 없음). 기존 종목·기사 행은 읽기만 한다(정정 공시 판정 대조).
"""

import re
import secrets
from datetime import date, datetime, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

import app.api.stocks as stocks_api
from app import stock_feed as sf
from app.ingest_validation import is_correction
from app.main import app

client = TestClient(app, raise_server_exceptions=False)
KST = ZoneInfo("Asia/Seoul")
NEWS_KEYS = {"ticker", "company_name", "asset_type", "total", "limit", "offset", "has_next", "total_pages", "filters",
             "freshness", "items"}
DISC_KEYS = NEWS_KEYS | {"supported", "message"}
FRESH_KEYS = {"last_collected_at", "collection_lag_weekdays", "stale", "company_latest_at", "last_collection"}
NEWS_ITEM_KEYS = {"id", "title", "summary", "source", "url", "published_at", "collected_at", "is_relevant", "related_tickers"}
DISC_ITEM_KEYS = {"id", "title", "disclosure_type", "rcept_no", "disclosed_date", "disclosed_at", "source_url", "is_correction",
                  "collected_at"}


class TestPure:
    def test_kst_range_is_inclusive_kst_days(self):
        start, end = sf.kst_range(date(2026, 11, 2), date(2026, 11, 3))
        assert start == datetime(2026, 11, 2, tzinfo=KST) and end == datetime(2026, 11, 4, tzinfo=KST)
        assert sf.kst_range(None, None) == (None, None)
        with pytest.raises(ValueError):
            sf.kst_range(date(2026, 11, 3), date(2026, 11, 2))

    def test_rcept_no(self):
        assert sf.rcept_no("https://dart.fss.or.kr/dsaf001/main.do?rcptNo=20261103900449") == "20261103900449"
        assert sf.rcept_no(None) is None and sf.rcept_no("https://example.com") is None

    def test_freshness_weekdays(self):
        last = datetime(2026, 11, 3, 10, 54, tzinfo=timezone.utc)  # KST 11-03(화) 19:54
        f = sf.freshness(last, None, date(2026, 11, 4))
        assert f["collection_lag_weekdays"] == 1 and f["stale"] is False and f["last_collected_at"].endswith("+09:00")
        assert sf.freshness(last, None, date(2026, 11, 10))["collection_lag_weekdays"] == 5
        assert sf.freshness(last, None, date(2026, 11, 10))["stale"] is True
        assert sf.freshness(last, None, date(2026, 11, 8))["collection_lag_weekdays"] == 3  # 일요일 -> 직전 평일 11-06
        none = sf.freshness(None, None, date(2026, 11, 4))
        assert none["stale"] is True and none["collection_lag_weekdays"] is None and set(none) == FRESH_KEYS

    @pytest.mark.parametrize("title", ["[기재정정]단일판매ㆍ공급계약체결", "[첨부정정]투자설명서", " [기재정정 ]사업보고서",
                                       "[발행조건확정]증권신고서", "단일판매ㆍ공급계약체결", "[정정]사업보고서", "기재정정 안내",
                                       "[기재정정]", "증권발행실적보고서[기재정정]"])
    def test_sql_regex_matches_python_rule(self, title):
        """SQL 필터용 정규식이 app.ingest_validation.is_correction과 같은 판정을 한다(파이썬 re로 같은 패턴을 확인)."""
        assert bool(re.match(sf.CORRECTION_SQL_RE, title)) == is_correction(title)

    def test_disclosure_date_is_kst(self):
        row = SimpleNamespace(id=1, title="[기재정정]x", disclosure_type="[기재정정]x",
                              source_url="https://dart.fss.or.kr/dsaf001/main.do?rcptNo=20261103800111",
                              disclosed_at=datetime(2026, 11, 2, 15, 0, tzinfo=timezone.utc), created_at=None)
        it = sf.disclosure_item(row)
        assert it["disclosed_date"] == date(2026, 11, 3) and it["disclosed_at"] == "2026-11-03T00:00:00+09:00"
        assert it["rcept_no"] == "20261103800111" and it["is_correction"] is True and set(it) == DISC_ITEM_KEYS

    def test_news_item_empty_summary_is_null(self):
        row = SimpleNamespace(id=1, title="t", content="", source=None, url=None, published_at=None, created_at=None, is_relevant=None)
        it = sf.news_item(row, [])
        assert it["summary"] is None and it["published_at"] is None and set(it) == NEWS_ITEM_KEYS


COMPANY = SimpleNamespace(id=1, ticker="005930", name="삼성전자", asset_type="stock")


@pytest.fixture
def patched(monkeypatch):
    calls = []

    def resolve(db, raw, allow_fuzzy=True):
        calls.append(("resolve", raw, allow_fuzzy))
        return SimpleNamespace(company=COMPANY, message=None)

    def page(db, company, limit, offset, date_from, date_to, order, *rest):
        calls.append(("page", limit, offset, date_from, date_to, order, *rest[:-1]))
        base = {"ticker": "005930", "company_name": "삼성전자", "asset_type": "stock", "total": 0, "limit": limit, "offset": offset,
                "has_next": False, "total_pages": 0, "filters": {"date_from": date_from, "date_to": date_to, "order": order},
                "freshness": sf.freshness(None, None, date(2026, 11, 4)), "items": []}
        if rest[:-1]:
            base["filters"]["correction"] = rest[0]
            base.update(supported=True, message=None)
        return base

    monkeypatch.setattr(stocks_api, "resolve_company", resolve)
    monkeypatch.setattr(stocks_api.stock_feed, "news_page", page)
    monkeypatch.setattr(stocks_api.stock_feed, "disclosure_page", page)
    return calls


class TestRouter:
    def test_news_defaults_and_strict_resolution(self, patched):
        r = client.get("/api/stocks/005930/news")  # 토큰 없이
        assert r.status_code == 200 and set(r.json()) == NEWS_KEYS
        assert patched == [("resolve", "005930", False), ("page", 20, 0, None, None, "desc")]

    def test_disclosure_params_reach_query(self, patched):
        r = client.get("/api/stocks/005930/disclosures?limit=50&offset=10&date_from=2026-11-01&date_to=2026-11-03&order=asc"
                       "&correction=exclude")
        assert r.status_code == 200 and set(r.json()) == DISC_KEYS
        assert patched[1] == ("page", 50, 10, date(2026, 11, 1), date(2026, 11, 3), "asc", "exclude")

    @pytest.mark.parametrize("path", ["news", "disclosures"])
    @pytest.mark.parametrize("qs", ["limit=0", "limit=51", "offset=-1", "order=latest", "date_from=2026-02-30",
                                    "date_from=2026-11-03&date_to=2026-11-02", "limit=abc"])
    def test_invalid_query_is_422_before_db(self, patched, path, qs):
        r = client.get(f"/api/stocks/005930/{path}?{qs}")
        assert r.status_code == 422 and r.json()["detail"][0]["loc"][0] == "query"
        assert patched == []

    def test_same_day_range_is_valid(self, patched):
        assert client.get("/api/stocks/005930/news?date_from=2026-11-03&date_to=2026-11-03").status_code == 200

    def test_bad_correction_value(self, patched):
        assert client.get("/api/stocks/005930/disclosures?correction=yes").status_code == 422

    @pytest.mark.parametrize("path", ["news", "disclosures"])
    def test_unknown_company_404(self, monkeypatch, path):
        monkeypatch.setattr(stocks_api, "resolve_company",
                            lambda db, raw, allow_fuzzy=True: SimpleNamespace(company=None, message="정확한 종목을 찾지 못했습니다."))
        r = client.get(f"/api/stocks/없는종목/{path}")
        assert r.status_code == 404 and r.json() == {"detail": "정확한 종목을 찾지 못했습니다."}

    @pytest.mark.parametrize("path", ["news", "disclosures"])
    def test_only_get(self, path):
        assert client.post(f"/api/stocks/005930/{path}").status_code == 405


# ---------- 개발 DB(테스트 전용 종목으로) ----------


def _utc(y, m, d, hh=0, mm=0, tz=KST):
    return datetime(y, m, d, hh, mm, tzinfo=tz).astimezone(timezone.utc)


@pytest.fixture(scope="module")
def seeded():
    """테스트 전용 종목 3개(주식 A·B, ETF E)와 뉴스·공시 행을 넣고, 끝나면 이 행들만 지운다(news FK는 SET NULL이라 먼저 지운다)."""
    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        db.execute(text("SELECT 1"))
    except Exception as e:
        db.close()
        pytest.skip(f"dev DB에 연결할 수 없음: {type(e).__name__}")
    tag = secrets.token_hex(3).upper()
    tick = {k: f"Z41{k}{tag}" for k in ("A", "B", "E")}
    ids = {}
    try:
        for k, asset in (("A", "stock"), ("B", "stock"), ("E", "etf")):
            ids[k] = db.execute(text("INSERT INTO company (ticker, name, market, asset_type) VALUES (:t, :n, NULL, :a) RETURNING id"),
                                {"t": tick[k], "n": f"사십일차테스트{k}{tag}", "a": asset}).scalar_one()
        u = f"https://example.test/41/{tag}"
        news = [  # (회사, 제목, url, 게시(KST), is_relevant, content)
            ("A", "기사1", f"{u}/1", _utc(2026, 11, 1, 9), True, "요약1"),
            ("A", "기사2 경계 직전", f"{u}/2", _utc(2026, 11, 2, 23, 59), True, None),
            ("A", "기사3 경계", f"{u}/3", _utc(2026, 11, 3, 0, 0), None, ""),
            ("A", "관련 없음", f"{u}/4", _utc(2026, 11, 3, 9), False, "x"),
            ("A", "중복 옛 행", f"{u}/5", _utc(2026, 11, 3, 10), True, "old"),
            ("A", "중복 새 행", f"{u}/5", _utc(2026, 11, 3, 10), True, "new"),
            ("B", "같은 기사 B", f"{u}/1", _utc(2026, 11, 1, 9), True, None),
            ("E", "같은 기사 E", f"{u}/1", _utc(2026, 11, 1, 9), True, None),
            ("B", "시각 없음 B", f"{u}/6", None, True, None),
        ]
        for k, title, url, at, rel, content in news:
            db.execute(text("INSERT INTO news (company_id, title, content, source, url, published_at, is_relevant) "
                            "VALUES (:c, :t, :x, 'example.test', :u, :p, :r)"),
                       {"c": ids[k], "t": title, "x": content, "u": url, "p": at, "r": rel})
        dart = "https://dart.fss.or.kr/dsaf001/main.do?rcptNo="  # 접수번호는 실제로 없는 9999년 번호(실데이터와 겹치지 않게)
        disc = [  # (제목, 접수번호, 접수일)
            ("단일판매ㆍ공급계약체결", "99991030000001", date(2026, 10, 30)),
            ("[기재정정]단일판매ㆍ공급계약체결", "99991102000002", date(2026, 11, 2)),
            ("기업설명회(IR)개최", "99991103000003", date(2026, 11, 3)),
            ("기업설명회(IR)개최 중복", "99991103000003", date(2026, 11, 3)),  # 같은 원문 URL 두 번 저장
        ]
        for title, no, d in disc:
            db.execute(text("INSERT INTO disclosure (company_id, title, disclosure_type, source_url, disclosed_at) "
                            "VALUES (:c, :t, :ty, :u, :d)"),
                       {"c": ids["A"], "t": title, "ty": title, "u": dart + no, "d": datetime.combine(d, datetime.min.time(), KST)})
        db.commit()
        yield SimpleNamespace(tick=tick, ids=ids, url=u)
    finally:
        db.rollback()
        cids = list(ids.values())
        if cids:
            db.execute(text("DELETE FROM news WHERE company_id = ANY(:c)"), {"c": cids})
            db.execute(text("DELETE FROM disclosure WHERE company_id = ANY(:c)"), {"c": cids})
            db.execute(text("DELETE FROM company WHERE id = ANY(:c)"), {"c": cids})
            db.commit()
        db.close()


@pytest.mark.integration
class TestDevDb:
    def test_news_list(self, seeded):
        b = client.get(f"/api/stocks/{seeded.tick['A']}/news").json()
        assert set(b) == NEWS_KEYS and set(b["freshness"]) == FRESH_KEYS
        titles = [i["title"] for i in b["items"]]
        # is_relevant=false 제외, 같은 종목 같은 URL은 새 행 하나만, 게시 시각 최신순(같으면 id 큰 것 먼저)
        assert titles == ["중복 새 행", "기사3 경계", "기사2 경계 직전", "기사1"] and b["total"] == 4
        first = b["items"][-1]
        assert set(first) == NEWS_ITEM_KEYS and first["summary"] == "요약1" and first["is_relevant"] is True
        assert first["published_at"] == "2026-11-01T09:00:00+09:00"
        assert first["related_tickers"] == sorted([seeded.tick["B"], seeded.tick["E"]])  # 같은 기사, 다른 종목 행은 그대로
        assert b["items"][1]["summary"] is None and b["items"][1]["is_relevant"] is None
        assert b["freshness"]["company_latest_at"] == "2026-11-03T10:00:00+09:00"

    def test_other_company_keeps_shared_article(self, seeded):
        b = client.get(f"/api/stocks/{seeded.tick['B']}/news").json()
        assert [i["title"] for i in b["items"]] == ["같은 기사 B", "시각 없음 B"]  # 게시 시각이 없는 행은 최신순에서도 맨 뒤
        assert b["items"][0]["related_tickers"] == sorted([seeded.tick["A"], seeded.tick["E"]])
        assert b["items"][1]["published_at"] is None
        asc = client.get(f"/api/stocks/{seeded.tick['B']}/news?order=asc").json()
        assert [i["title"] for i in asc["items"]] == ["같은 기사 B", "시각 없음 B"]

    def test_news_pagination_and_order(self, seeded):
        t = seeded.tick["A"]
        p1 = client.get(f"/api/stocks/{t}/news?limit=3").json()
        p2 = client.get(f"/api/stocks/{t}/news?limit=3&offset=3").json()
        assert (p1["has_next"], p1["total_pages"], len(p1["items"])) == (True, 2, 3)
        assert (p2["has_next"], len(p2["items"])) == (False, 1)
        asc = client.get(f"/api/stocks/{t}/news?order=asc").json()
        assert [i["id"] for i in asc["items"]] == [i["id"] for i in reversed(p1["items"] + p2["items"])]
        past = client.get(f"/api/stocks/{t}/news?offset=100").json()
        assert past["items"] == [] and past["total"] == 4 and past["has_next"] is False

    def test_news_date_filter_kst_boundary(self, seeded):
        t = seeded.tick["A"]
        d2 = client.get(f"/api/stocks/{t}/news?date_from=2026-11-02&date_to=2026-11-02").json()
        assert [i["title"] for i in d2["items"]] == ["기사2 경계 직전"]
        d3 = client.get(f"/api/stocks/{t}/news?date_from=2026-11-03").json()
        assert [i["title"] for i in d3["items"]] == ["중복 새 행", "기사3 경계"]
        assert d3["filters"] == {"date_from": "2026-11-03", "date_to": None, "order": "desc"}
        empty = client.get(f"/api/stocks/{t}/news?date_to=2026-10-01").json()
        assert empty["items"] == [] and empty["total"] == 0 and empty["total_pages"] == 0

    def test_disclosures(self, seeded):
        t = seeded.tick["A"]
        b = client.get(f"/api/stocks/{t}/disclosures").json()
        assert set(b) == DISC_KEYS and b["supported"] is True and b["message"] is None
        assert b["total"] == 3  # 같은 원문 URL 중복은 하나만
        top = b["items"][0]
        assert set(top) == DISC_ITEM_KEYS and top["title"] == "기업설명회(IR)개최 중복"
        assert top["disclosed_date"] == "2026-11-03" and top["disclosed_at"] == "2026-11-03T00:00:00+09:00"
        assert top["rcept_no"] == "99991103000003"
        assert [i["is_correction"] for i in b["items"]] == [False, True, False]
        only = client.get(f"/api/stocks/{t}/disclosures?correction=only").json()
        excl = client.get(f"/api/stocks/{t}/disclosures?correction=exclude").json()
        assert [i["title"] for i in only["items"]] == ["[기재정정]단일판매ㆍ공급계약체결"]
        assert only["total"] + excl["total"] == b["total"] and all(not i["is_correction"] for i in excl["items"])
        ranged = client.get(f"/api/stocks/{t}/disclosures?date_from=2026-10-30&date_to=2026-11-02&order=asc").json()
        assert [i["disclosed_date"] for i in ranged["items"]] == ["2026-10-30", "2026-11-02"]

    def test_etf_disclosures_not_supported(self, seeded):
        b = client.get(f"/api/stocks/{seeded.tick['E']}/disclosures").json()
        assert b["supported"] is False and b["items"] == [] and b["total"] == 0 and seeded.tick["E"] in b["message"]
        assert client.get(f"/api/stocks/{seeded.tick['E']}/news").json()["total"] == 1  # ETF 뉴스는 된다

    def test_strict_resolution(self, seeded):
        """비슷한 이름으로 다른 종목을 보여주지 않는다(allow_fuzzy=False). 정확한 종목명은 된다."""
        name = f"사십일차테스트A{seeded.tick['A'][4:]}"
        assert client.get(f"/api/stocks/{name}/news").json()["ticker"] == seeded.tick["A"]
        r = client.get(f"/api/stocks/{name}X/news")
        assert r.status_code == 404 and isinstance(r.json()["detail"], str)

    def test_query_counts(self, seeded):
        from sqlalchemy import event

        from app.db.session import engine

        def count(url):
            n = []
            listener = lambda *a, **k: n.append(a[2] if len(a) > 2 else "")  # noqa: E731
            event.listen(engine, "before_cursor_execute", listener)
            try:
                assert client.get(url).status_code == 200
            finally:
                event.remove(engine, "before_cursor_execute", listener)
            assert all(str(q).lstrip().upper().startswith("SELECT") for q in n)
            return len(n)

        # 종목 해석 1 + 건수·페이지·같은 URL·신선도·수집 시도(뉴스 5, 공시 4). 페이지 크기와 무관(N+1 없음)
        assert count(f"/api/stocks/{seeded.tick['A']}/news?limit=50") == 6
        assert count(f"/api/stocks/{seeded.tick['A']}/disclosures?limit=50") == 5
        assert count(f"/api/stocks/{seeded.tick['E']}/disclosures") == 3

    def test_sql_correction_filter_agrees_with_python_on_stored_titles(self, dev_db):
        """저장된 모든 공시 제목(읽기만)에서 PostgreSQL 정규식 판정과 is_correction이 같다. CI 빈 DB면 0건이라 자명하게 맞는다."""
        rows = dev_db.execute(text("SELECT title, title ~ :re FROM disclosure"), {"re": sf.CORRECTION_SQL_RE}).all()
        assert [t for t, sql in rows if bool(sql) != is_correction(t)] == []
