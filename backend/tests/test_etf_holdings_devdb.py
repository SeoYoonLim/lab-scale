"""ETF 구성종목을 실제 dev DB로 검증한다: 저장(멱등)·API·채팅 템플릿·마이그레이션 왕복.

- 저장·API 계약 테스트는 captest_ 접두어의 가짜 ETF(company 한 행)를 직접 만들어 쓰고 끝나면 지운다(수집 데이터가 없는 CI에서도 돈다).
- 실제 수집 데이터(etf_holding, scripts.collect_etf_holdings로 채운 값)를 읽는 테스트만 needs_collected_data다. 그 값과 API 응답이 같은지
  SQL로 다시 계산해 대조한다.
- 마이그레이션 왕복은 MIGRATION_TEST_DATABASE_URL이 가리키는 별도 서버의 임시 DB에서만 한다(dev DB에는 하지 않는다).
"""

import os
import secrets
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

import app.agent as agent
from app import etf_holdings as E
from app.db.session import SessionLocal
from app.etf import constituents_answer
from app.etf_holdings_db import chat_views, holdings_view, save_snapshot
from app.main import app
from app.models import Company, EtfHolding
from evals.metrics import forbidden_hits
from test_auth_revoke_devdb import BACKEND, _migration_server_url

client = TestClient(app, raise_server_exceptions=False)

pytestmark = pytest.mark.integration

KST_TODAY = datetime.now(timezone(timedelta(hours=9))).date()


def snap(as_of: date, rows, source=E.KODEX) -> E.Snapshot:
    return E.Snapshot(source=source, as_of=as_of, holdings=[E.Holding(n, c, w) for n, c, w in rows])


@pytest.fixture
def fake_etf(dev_db):
    """captest_ 접두어 가짜 ETF 하나(ticker는 임의). 끝나면 company와 etf_holding(FK CASCADE)을 지운다."""
    made = []

    def _make(name="KODEX captest", asset_type="etf"):
        ticker = ("T" + secrets.token_hex(4))[:9].upper()
        c = Company(ticker=ticker, name=f"{name} {secrets.token_hex(2)}", asset_type=asset_type)
        dev_db.add(c)
        dev_db.commit()
        made.append(c.id)
        return c

    yield _make
    dev_db.rollback()
    for cid in made:
        dev_db.query(Company).filter(Company.id == cid).delete()
    dev_db.commit()


class TestStoreAndApi:
    def test_save_is_idempotent_and_replaces_the_same_date(self, dev_db, fake_etf):
        c = fake_etf()
        rows = [("삼성전자", "005930", 30.0), ("SK하이닉스", "000660", 20.0), ("해외종목", None, 10.0)]
        assert save_snapshot(dev_db, c.id, snap(date(2026, 10, 28), rows)) == 3
        assert save_snapshot(dev_db, c.id, snap(date(2026, 10, 28), rows)) == 3  # 다시 수집해도 그대로
        assert dev_db.query(EtfHolding).filter(EtfHolding.company_id == c.id).count() == 3
        save_snapshot(dev_db, c.id, snap(date(2026, 10, 28), rows[:1]))  # 같은 기준일 재수집은 통째로 교체
        assert dev_db.query(EtfHolding).filter(EtfHolding.company_id == c.id).count() == 1
        save_snapshot(dev_db, c.id, snap(date(2026, 10, 29), rows))  # 다른 기준일은 따로 쌓인다
        assert dev_db.query(EtfHolding).filter(EtfHolding.company_id == c.id).count() == 4

    def test_unique_key_treats_null_codes_as_equal(self, dev_db, fake_etf):
        from sqlalchemy.exc import IntegrityError

        c = fake_etf()
        for _ in range(2):
            dev_db.add(EtfHolding(company_id=c.id, as_of_date=date(2026, 10, 28), holding_name="코드없음", holding_code=None,
                                  weight_pct=1, source=E.KODEX))
        with pytest.raises(IntegrityError):
            dev_db.commit()
        dev_db.rollback()

    def test_view_and_api_contract(self, dev_db, fake_etf):
        c = fake_etf()
        save_snapshot(dev_db, c.id, snap(KST_TODAY, [("A", "000001", 60.0), ("B", "000002", 30.0), ("C", None, 10.0)]))
        r = client.get(f"/api/etf/{c.ticker}/holdings?limit=2")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ticker"] == c.ticker and body["limit"] == 2 and body["holding_count"] == 3
        assert body["total_weight_pct"] == 100.0 and body["top_weight_pct"] == 90.0 and body["stale"] is False
        assert [(i["rank"], i["name"], i["code"], i["weight_pct"]) for i in body["items"]] == [(1, "A", "000001", 60.0), (2, "B", "000002", 30.0)]
        assert body["as_of_date"] == KST_TODAY.isoformat() and body["source"] == E.SOURCES[E.KODEX] and body["data_warnings"] == []
        assert body["collected_at"] is not None and body["message"] is None
        assert client.get(f"/api/etf/{c.ticker}/holdings?limit=51").status_code == 422

    def test_latest_date_wins_and_old_date_is_stale(self, dev_db, fake_etf):
        c = fake_etf()
        save_snapshot(dev_db, c.id, snap(date(2026, 9, 1), [("옛날", "1", 100.0)]))
        v = holdings_view(dev_db, c, 10, today=date(2026, 10, 29))
        assert v["items"][0]["name"] == "옛날" and v["stale"] is True and "2026-09-01" in v["stale_notice"]
        save_snapshot(dev_db, c.id, snap(date(2026, 10, 29), [("새것", "2", 100.0)]))
        v = holdings_view(dev_db, c, 10, today=date(2026, 10, 29))
        assert v["items"][0]["name"] == "새것" and v["stale"] is False and v["holding_count"] == 1

    def test_weight_sum_warning(self, dev_db, fake_etf):
        c = fake_etf()
        save_snapshot(dev_db, c.id, snap(KST_TODAY, [("A", "1", 50.0)]))
        body = client.get(f"/api/etf/{c.ticker}/holdings").json()
        assert body["total_weight_pct"] == 50.0 and body["data_warnings"][0]["kind"] == "weight_sum"

    def test_etf_without_data_is_200_with_message(self, fake_etf):
        c = fake_etf(name="RISE captest")
        body = client.get(f"/api/etf/{c.ticker}/holdings").json()
        assert body["items"] == [] and body["as_of_date"] is None and body["message"] == E.MSG_NO_DATA
        c2 = fake_etf(name="KODEX captest")
        assert client.get(f"/api/etf/{c2.ticker}/holdings").json()["message"] == E.MSG_NOT_COLLECTED

    def test_unsupported_issuer_keeps_the_refusal(self, fake_etf):
        ace = fake_etf(name="ACE captest")
        resp, _ = agent._answer_etf_constituents(f"{ace.name} 구성종목 알려줘", [ace])
        assert resp["answer"] == constituents_answer([ace.name])

    def test_stock_and_unknown(self, fake_etf):
        stock = fake_etf(name="captest주식", asset_type="stock")
        r = client.get(f"/api/etf/{stock.ticker}/holdings")
        assert r.status_code == 404 and "ETF가 아니라" in r.json()["detail"]
        r = client.get("/api/etf/ZZZZNOPE/holdings")
        assert r.status_code == 404

    def test_query_count_is_fixed(self, dev_db, fake_etf):
        from sqlalchemy import event

        from app.db.session import engine

        c = fake_etf()
        save_snapshot(dev_db, c.id, snap(KST_TODAY, [(f"S{i}", f"{i:06d}", 1.0) for i in range(300)]))
        _ = (c.id, c.ticker, c.name, c.asset_type)  # 커밋으로 만료된 fixture 객체를 미리 읽어 둔다(그 SELECT를 세지 않게)
        statements = []

        def on_execute(conn, cursor, statement, *_):
            statements.append(statement)

        event.listen(engine, "before_cursor_execute", on_execute)
        db = SessionLocal()
        try:
            holdings_view(db, c, 50)
        finally:
            db.close()
            event.remove(engine, "before_cursor_execute", on_execute)
        assert len(statements) == 3

    def test_chat_with_data_and_missing(self, dev_db, fake_etf):
        has, missing = fake_etf(), fake_etf(name="ACE captest")
        save_snapshot(dev_db, has.id, snap(KST_TODAY, [("삼성전자", "005930", 70.0), ("현대차", "005380", 30.0)]))
        db = SessionLocal()
        try:
            views, names = chat_views(db, [has, missing])
        finally:
            db.close()
        assert [v["name"] for v in views] == [has.name] and names == [missing.name]
        resp, records = agent._answer_etf_constituents(f"{has.name}이랑 {missing.name} 구성종목 알려줘", [has, missing])
        assert "삼성전자 (005930) 70.00%" in resp["answer"] and f"{missing.name}의 구성종목과 편입 비중 데이터는 이 서비스에서 제공하지 않습니다" in resp["answer"]
        assert forbidden_hits(resp["answer"]) == []


@pytest.mark.needs_collected_data
class TestCollectedData:
    """scripts.collect_etf_holdings로 실제 수집한 값을 읽는다."""

    def _etf(self, dev_db, ticker):
        c = dev_db.query(Company).filter(Company.ticker == ticker).one()
        dev_db.rollback()
        return c

    @pytest.mark.parametrize("ticker, top_name", [("069500", "삼성전자"), ("133690", "NVIDIA Corp")])
    def test_api_matches_an_independent_sql_recalculation(self, dev_db, ticker, top_name):
        body = client.get(f"/api/etf/{ticker}/holdings?limit=50").json()
        if not body["items"]:
            pytest.skip(f"{ticker} 수집 데이터 없음(python -m scripts.collect_etf_holdings 필요; 운용사 사이트가 429로 막으면 수집 불가)")
        row = dev_db.execute(text(
            "SELECT h.as_of_date, count(*), round(sum(h.weight_pct)::numeric, 2) FROM etf_holding h JOIN company c ON c.id = h.company_id "
            "WHERE c.ticker = :t AND h.as_of_date = (SELECT max(as_of_date) FROM etf_holding x WHERE x.company_id = c.id) "
            "GROUP BY h.as_of_date"), {"t": ticker}).one()
        top = dev_db.execute(text(
            "SELECT h.holding_name, h.weight_pct FROM etf_holding h JOIN company c ON c.id = h.company_id "
            "WHERE c.ticker = :t AND h.as_of_date = :d ORDER BY h.weight_pct DESC, h.holding_name LIMIT 50"), {"t": ticker, "d": row[0]}).all()
        dev_db.rollback()
        assert body["as_of_date"] == row[0].isoformat() and body["holding_count"] == row[1] and body["total_weight_pct"] == float(row[2])
        assert [(i["name"], i["weight_pct"]) for i in body["items"]] == [(n, float(w)) for n, w in top]
        assert body["items"][0]["name"] == top_name
        weights = [i["weight_pct"] for i in body["items"]]
        assert weights == sorted(weights, reverse=True) and body["top_weight_pct"] == pytest.approx(sum(weights), abs=0.01)
        assert 95 <= body["total_weight_pct"] <= 105 and body["data_warnings"] == []

    def test_leverage_and_inverse_have_expected_sums(self, dev_db):
        lev = client.get("/api/etf/122630/holdings").json()
        inv = client.get("/api/etf/114800/holdings").json()
        if not lev["items"] or not inv["items"]:
            pytest.skip("KODEX 레버리지·인버스 수집 데이터 없음(삼성 사이트 429 차단 시 수집 불가)")
        assert 190 <= lev["total_weight_pct"] <= 210 and lev["data_warnings"] == []
        assert 95 <= inv["total_weight_pct"] <= 105 and inv["items"][0]["name"].endswith("지수선물")

    def test_every_collected_etf_has_one_issuer_and_sane_weights(self, dev_db):
        rows = dev_db.execute(text(
            "SELECT c.name, count(DISTINCT h.source), min(h.weight_pct) FROM etf_holding h JOIN company c ON c.id = h.company_id GROUP BY c.name")).all()
        dev_db.rollback()
        assert rows, "수집 데이터가 하나도 없음: python -m scripts.collect_etf_holdings 필요"
        for name, sources, min_weight in rows:
            # 음수 비중은 매도 콜옵션·TRS 같은 파생 포지션이다(작다). 큰 음수는 파싱 오류의 신호다.
            assert sources == 1 and float(min_weight) > -1.0, name
            assert E.issuer_supported(name), name
        assert dev_db.execute(text("SELECT count(*) FROM etf_holding WHERE holding_code IN ('', '-')")).scalar() == 0
        dev_db.rollback()

    def test_chat_answers_with_real_data(self, dev_db):
        companies = [self._etf(dev_db, "069500")]
        if not client.get("/api/etf/069500/holdings").json()["items"]:
            pytest.skip("KODEX 200 수집 데이터 없음(삼성 사이트 429 차단 시 수집 불가)")
        resp, _ = agent._answer_etf_constituents("KODEX 200 구성종목 비중 알려줘", companies)
        assert "삼성전자 (005930)" in resp["answer"] and "기준" in resp["answer"] and "제공하지 않습니다" not in resp["answer"]
        assert forbidden_hits(resp["answer"]) == []


# ---------- 마이그레이션 왕복(별도 서버의 임시 DB) ----------


class TestMigrationRoundTrip:
    def test_etf_holding_upgrade_downgrade_upgrade(self):
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

        def table():
            with psycopg.connect(target_pg) as c:
                cols = c.execute("SELECT column_name, is_nullable FROM information_schema.columns WHERE table_name = 'etf_holding' "
                                 "ORDER BY ordinal_position").fetchall()
                idx = c.execute("SELECT indexname FROM pg_indexes WHERE tablename = 'etf_holding' ORDER BY 1").fetchall()
                ver = c.execute("SELECT version_num FROM alembic_version").fetchone()[0]
            return cols, [i[0] for i in idx], ver

        with psycopg.connect(admin, autocommit=True) as c:
            c.execute(f'CREATE DATABASE "{name}"')
        try:
            alembic("upgrade", "e2a9c4f71b03")
            assert table()[0] == []
            alembic("upgrade", "f4b81d29c6e5")  # head가 아니라 이 리비전까지(뒤에 마이그레이션이 더 생겨도 그대로)
            cols, idx, ver = table()
            assert ver == "f4b81d29c6e5"
            assert dict(cols) == {"id": "NO", "company_id": "NO", "as_of_date": "NO", "holding_name": "NO", "holding_code": "YES",
                                  "weight_pct": "NO", "source": "NO", "collected_at": "NO"}
            assert set(idx) == {"idx_etf_holding_company_date", "etf_holding_pkey", "uq_etf_holding_etf_date_holding"}
            with psycopg.connect(target_pg, autocommit=True) as c:  # NULLS NOT DISTINCT 유니크가 실제로 DB에서 동작
                c.execute("INSERT INTO company (ticker, name, asset_type) VALUES ('T1', 'KODEX t', 'etf')")
                cid = c.execute("SELECT id FROM company WHERE ticker = 'T1'").fetchone()[0]
                ins = "INSERT INTO etf_holding (company_id, as_of_date, holding_name, holding_code, weight_pct, source) VALUES (%s, '2026-10-28', 'x', NULL, 1, 's')"
                c.execute(ins, (cid,))
                with pytest.raises(psycopg.errors.UniqueViolation):
                    c.execute(ins, (cid,))
                c.execute("DELETE FROM company WHERE id = %s", (cid,))  # ON DELETE CASCADE
                assert c.execute("SELECT count(*) FROM etf_holding").fetchone()[0] == 0
            alembic("downgrade", "-1")
            cols, idx, ver = table()
            assert cols == [] and idx == [] and ver == "e2a9c4f71b03"
            alembic("upgrade", "f4b81d29c6e5")  # head가 아니라 이 리비전까지(뒤에 마이그레이션이 더 생겨도 그대로)
            assert table()[2] == "f4b81d29c6e5"
        finally:
            with psycopg.connect(admin, autocommit=True) as c:
                c.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
