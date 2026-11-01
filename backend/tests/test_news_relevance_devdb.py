"""뉴스 관련성 표시가 채팅·RAG·브리핑·사건 반응에 적용되는지 dev DB로 확인한다. 임시 행을 넣고 끝에 지운다(기존 행은 건드리지 않는다).

마이그레이션 왕복은 MIGRATION_TEST_DATABASE_URL이 가리키는 별도 서버의 임시 DB에서만 한다(dev DB에는 하지 않는다)."""

import os
import secrets
import subprocess
import sys
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import event, text

from app.briefing_db import get_briefing
from app.db.session import SessionLocal, engine
from app.models import Company, News
from app.tools.news_tool import news_tool
from app.tools.rag_search_tool import _top_unique
from test_auth_revoke_devdb import BACKEND, _migration_server_url

pytestmark = pytest.mark.integration

TAG = "captest-news-relevance"


@pytest.fixture
def seeded(dev_db):
    """태성에 임시 뉴스 3건(관련 없음, 관련 있음, 미판정)을 가장 최근 날짜로 넣는다."""
    company = dev_db.query(Company).filter(Company.ticker == "323280").one()
    now = datetime.now(timezone.utc) + timedelta(days=1)
    rows = [News(company_id=company.id, title=f"{TAG} {label}", content="내용", url=f"https://{TAG}.example/{secrets.token_hex(4)}",
                 published_at=now, is_relevant=flag, embedding=[0.1] * 1024)
            for label, flag in (("hidden", False), ("kept", True), ("unjudged", None))]
    dev_db.add_all(rows)
    dev_db.commit()
    yield company
    dev_db.rollback()
    dev_db.execute(text("DELETE FROM news WHERE url LIKE :u"), {"u": f"https://{TAG}.example/%"})
    dev_db.commit()


def _titles(items):
    return [i["title"] for i in items if i["title"].startswith(TAG)]


def test_news_tool_skips_rows_marked_not_relevant(seeded):
    out = news_tool("태성", limit=5)
    assert out["found"] is True
    assert sorted(_titles(out["news"])) == [f"{TAG} kept", f"{TAG} unjudged"]


def test_rag_skips_rows_marked_not_relevant(seeded, dev_db):
    rows = _top_unique(dev_db, News, News.url, [0.1] * 1024, seeded, 5)
    assert sorted(r.title for r, _ in rows if r.title.startswith(TAG)) == [f"{TAG} kept", f"{TAG} unjudged"]


def test_event_reactions_skip_rows_marked_not_relevant(seeded, dev_db):
    statements = []

    def on_execute(conn, cursor, statement, *_):
        statements.append(statement)

    from app.event_reaction_service import build_event_reactions

    event.listen(engine, "before_cursor_execute", on_execute)
    try:
        build_event_reactions(dev_db, seeded, "all", 1)
    finally:
        event.remove(engine, "before_cursor_execute", on_execute)
    assert any("is_relevant IS NOT false" in s for s in statements if "FROM news" in s)


def test_news_tool_says_not_found_when_only_hidden_rows_remain(dev_db):
    """임시 종목에 관련 없는 기사만 있으면 수집 안내가 아니라 "찾지 못했다"고 답한다."""
    ticker = f"Z{secrets.token_hex(2).upper()}9"
    name = f"캡테스트{secrets.token_hex(3)}"
    company = Company(ticker=ticker, name=name, asset_type="stock")
    dev_db.add(company)
    dev_db.flush()
    dev_db.add(News(company_id=company.id, title=f"{TAG} hidden", url=f"https://{TAG}.example/x{ticker}", is_relevant=False,
                    published_at=datetime.now(timezone.utc)))
    dev_db.commit()
    try:
        out = news_tool(name, limit=5)
        assert out["found"] is False and "관련 뉴스를 찾지 못했습니다" in out["message"]
    finally:
        dev_db.execute(text("DELETE FROM news WHERE company_id = :c"), {"c": company.id})
        dev_db.execute(text("DELETE FROM company WHERE id = :c"), {"c": company.id})
        dev_db.commit()


class TestBriefing:
    def _run(self, owner):
        statements = []

        def on_execute(conn, cursor, statement, *_):
            statements.append(statement)

        db = SessionLocal()
        event.listen(engine, "before_cursor_execute", on_execute)
        try:
            return get_briefing(db, owner, 3), statements
        finally:
            event.remove(engine, "before_cursor_execute", on_execute)
            db.close()

    def test_filter_is_only_a_condition_and_query_count_is_unchanged(self, seeded, dev_db):
        owner = f"user:captest-{secrets.token_hex(4)}"
        from app.models import Watchlist

        dev_db.add(Watchlist(device_id=owner, company_id=seeded.id))
        dev_db.commit()
        try:
            brief, statements = self._run(owner)
            news_queries = [s for s in statements if "FROM news" in s]
            assert len(news_queries) == 1 and "is_relevant IS NOT false" in news_queries[0]
            titles = [n["title"] for item in brief["watchlist"] for n in item.get("news") or []]
            assert f"{TAG} hidden" not in titles
            assert f"{TAG} kept" in titles
            # 표시가 없는 행만 있을 때와 같은 쿼리 수: 조건만 추가했다(종목 수와 무관한 기존 고정 수)
            _, again = self._run(owner)
            assert len(again) == len(statements)
        finally:
            dev_db.execute(text("DELETE FROM watchlist WHERE device_id = :o"), {"o": owner})
            dev_db.commit()


@pytest.mark.needs_collected_data
def test_contaminated_company_returns_only_its_own_articles_after_marking():
    """표시를 적용한 dev DB에서 태성 뉴스에 동명 기사가 나오지 않는다(개발 DB 수집 데이터가 필요하다)."""
    out = news_tool("태성", limit=20)
    assert out["found"] is True
    text_all = " ".join(f"{n['title']} {n['content']}" for n in out["news"])
    assert "이태성" not in text_all and "태성무역" not in text_all and "태성자산운용" not in text_all
    assert all("태성" in f"{n['title']} {n['content']}" for n in out["news"])


class TestMigrationRoundTrip:
    def test_news_is_relevant_upgrade_downgrade_upgrade(self):
        import psycopg

        server = _migration_server_url()
        name = f"captest_migr_{secrets.token_hex(4)}"
        admin = server.set(drivername="postgresql").render_as_string(hide_password=False)
        target = server.set(database=name)
        target_sa = target.render_as_string(hide_password=False)
        target_pg = target.set(drivername="postgresql").render_as_string(hide_password=False)

        def alembic(*args):
            env = {**os.environ, "DATABASE_URL": target_sa}
            r = subprocess.run([sys.executable, "-m", "alembic", *args], cwd=BACKEND, env=env, capture_output=True, text=True, timeout=180)
            assert r.returncode == 0, r.stderr[-2000:]

        def column():
            with psycopg.connect(target_pg) as c:
                return c.execute("SELECT is_nullable FROM information_schema.columns "
                                 "WHERE table_name = 'news' AND column_name = 'is_relevant'").fetchall()

        with psycopg.connect(admin, autocommit=True) as c:
            c.execute(f'CREATE DATABASE "{name}"')
        try:
            alembic("upgrade", "d4f8a1c6b2e9")
            assert column() == []
            with psycopg.connect(target_pg, autocommit=True) as c:
                c.execute("INSERT INTO news (title) VALUES ('old row')")
            alembic("upgrade", "a9d3f6c12b47")
            assert column() == [("YES",)]
            with psycopg.connect(target_pg) as c:
                assert c.execute("SELECT is_relevant FROM news WHERE title = 'old row'").fetchall() == [(None,)]
            alembic("downgrade", "d4f8a1c6b2e9")
            assert column() == []
            with psycopg.connect(target_pg) as c:
                assert c.execute("SELECT count(*) FROM news").fetchone()[0] == 1
            alembic("upgrade", "a9d3f6c12b47")
            assert column() == [("YES",)]
        finally:
            with psycopg.connect(admin, autocommit=True) as c:
                c.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
