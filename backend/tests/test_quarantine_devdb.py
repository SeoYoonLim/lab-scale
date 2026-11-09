"""36차(2026-11-08): 저장 전 검증·격리를 실제 dev DB로 확인한다. 외부 API는 부르지 않는다(수집 함수의 원격 호출을 가짜로 바꾼다).

- 테스트 데이터는 captest_q36 접두어의 가짜 종목(company 한 행)과 그 시세·뉴스·공시·ETF 구성, 그 키의 격리 기록뿐이고 끝나면 지운다.
  기존 종목·수집 데이터·사용자 데이터는 읽거나 바꾸지 않는다.
- 마이그레이션 왕복은 MIGRATION_TEST_DATABASE_URL의 별도 서버 임시 DB에서만 한다(없으면 skip).
"""

import os
import secrets
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone

import pandas as pd
import pytest
from sqlalchemy import text

from app import collectors, etf_holdings as E, etf_holdings_collect as EC, quarantine as Q
from app.ingest_validation import KST, Checked, Verdict
from app.models import Company, DataIngestionQuarantine, DataRefreshRun, Disclosure, EtfHolding, News, StockPrice
from test_auth_revoke_devdb import BACKEND, _migration_server_url

pytestmark = pytest.mark.integration

D1, D2, D3, D4 = date(2026, 11, 2), date(2026, 11, 3), date(2026, 11, 4), date(2026, 11, 5)  # 모두 거래일


@pytest.fixture
def table_ready(dev_db):
    try:
        dev_db.execute(text("SELECT 1 FROM data_ingestion_quarantine LIMIT 1"))
    except Exception:
        dev_db.rollback()
        pytest.skip("data_ingestion_quarantine 표가 없음(alembic upgrade head 필요)")
    return dev_db


@pytest.fixture
def fake_co(table_ready):
    """가짜 종목(ticker는 Q + 16진 5자리). 끝나면 그 종목의 뉴스(SET NULL이라 직접)·company(CASCADE)·격리 기록을 지운다."""
    db = table_ready
    made = []

    def _make(name="captest_q36", asset_type="stock"):
        ticker = ("Q" + secrets.token_hex(3)[:5]).upper()
        c = Company(ticker=ticker, name=f"{name} {ticker}", asset_type=asset_type)
        db.add(c)
        db.commit()
        made.append(c)
        return c

    yield _make
    db.rollback()
    for c in made:
        db.execute(text("DELETE FROM news WHERE company_id = :i"), {"i": c.id})
        db.execute(text("DELETE FROM data_ingestion_quarantine WHERE source_key LIKE :k OR payload->>'ticker' = :t"),
                   {"k": f"%:{c.ticker}:%", "t": c.ticker})
        db.execute(text("DELETE FROM company WHERE id = :i"), {"i": c.id})
    db.commit()


def frame(rows):
    idx = pd.DatetimeIndex([pd.Timestamp(d) for d, *_ in rows])
    return pd.DataFrame({"Open": [r[1] for r in rows], "High": [r[1] for r in rows], "Low": [r[1] for r in rows],
                         "Close": [r[1] for r in rows], "Volume": [r[2] for r in rows], "Change": [0.0] * len(rows)}, index=idx)


def q_rows(db, ticker):
    db.expire_all()
    return db.query(DataIngestionQuarantine).filter(DataIngestionQuarantine.source_key.like(f"%:{ticker}:%")) \
        .order_by(DataIngestionQuarantine.source_key).all()


def close_of(db, company_id, d):
    db.expire_all()
    v = db.query(StockPrice.close_price).filter(StockPrice.company_id == company_id, StockPrice.price_date == d).scalar()
    return None if v is None else float(v)


class TestPriceIngestion:
    def test_existing_data_protected_batch_kept_and_requarantine_deduped(self, dev_db, fake_co, monkeypatch):  # 15·16·17·18
        co = fake_co()
        dev_db.add(StockPrice(company_id=co.id, price_date=D2, open_price=100, high_price=100, low_price=100, close_price=100, volume=10))
        dev_db.commit()
        monkeypatch.setattr(collectors, "_read_fdr", lambda *a, **k: frame([(D1, 98, 5), (D2, 40, 5), (D3, 0, 5), (D4, 99, 5)]))
        r = collectors.fetch_and_save_stock(co.ticker, co.name, start_date="2026-11-02", end_date="2026-11-05")
        assert r.ok and r.quarantined == 2 and r.fetched == 4
        assert close_of(dev_db, co.id, D2) == 100.0  # 기존 정상 값 유지(40으로 덮어쓰지 않음)
        assert close_of(dev_db, co.id, D1) == 98.0 and close_of(dev_db, co.id, D4) == 99.0  # 정상 레코드는 저장
        assert close_of(dev_db, co.id, D3) is None  # 0원은 저장하지 않음
        rows = q_rows(dev_db, co.ticker)
        assert [(x.error_code, x.validation_status, x.occurrences) for x in rows] == [
            ("conflicts_existing", "quarantined", 1), ("close_nonpositive", "quarantined", 1)]
        # 같은 데이터를 다시 수집: 저장 결과는 같고 격리 기록은 새로 생기지 않는다(occurrences만 증가)
        collectors.fetch_and_save_stock(co.ticker, co.name, start_date="2026-11-02", end_date="2026-11-05")
        rows = q_rows(dev_db, co.ticker)
        assert len(rows) == 2 and [x.occurrences for x in rows] == [2, 2] and close_of(dev_db, co.id, D2) == 100.0

    def test_invalid_ticker_does_not_create_company(self, dev_db, table_ready, monkeypatch):  # 3
        bad = "BAD-" + secrets.token_hex(2)
        monkeypatch.setattr(collectors, "_read_fdr", lambda *a, **k: frame([(D1, 98, 5)]))
        try:
            r = collectors.fetch_and_save_stock(bad, "captest_q36 bad", start_date="2026-11-02", end_date="2026-11-02")
            assert r.ok and r.quarantined == 1 and r.saved == 0
            assert dev_db.query(Company).filter(Company.ticker == bad).count() == 0
            assert len(q_rows(dev_db, bad)) == 1
        finally:
            dev_db.execute(text("DELETE FROM data_ingestion_quarantine WHERE source_key LIKE :k"), {"k": f"%:{bad}:%"})
            dev_db.commit()

    def test_revalidate_and_reprocess_after_fix(self, dev_db, fake_co, monkeypatch):  # 19·20
        co = fake_co()
        dev_db.add(StockPrice(company_id=co.id, price_date=D2, open_price=100, high_price=100, low_price=100, close_price=100, volume=10))
        dev_db.commit()
        monkeypatch.setattr(collectors, "_read_fdr", lambda *a, **k: frame([(D2, 40, 5), (D3, 0, 5)]))
        collectors.fetch_and_save_stock(co.ticker, co.name, start_date="2026-11-03", end_date="2026-11-04")
        conflict = next(x for x in q_rows(dev_db, co.ticker) if x.error_code == "conflicts_existing")
        zero = next(x for x in q_rows(dev_db, co.ticker) if x.error_code == "close_nonpositive")
        assert Q.revalidate(dev_db, conflict.id) == "still_quarantined"  # 기존 값이 그대로면 여전히 격리
        assert Q.revalidate(dev_db, zero.id) == "still_quarantined"
        assert Q.reprocess(dev_db, conflict.id, apply=True).startswith("skipped:open")  # 재검증 전에는 넣지 않음
        # 운영자가 원인을 확인하고 기존 행을 정리했다고 가정(예: 액면분할로 과거 값이 바뀜)
        dev_db.query(StockPrice).filter(StockPrice.company_id == co.id, StockPrice.price_date == D2).delete()
        dev_db.commit()
        assert Q.revalidate(dev_db, conflict.id) == "revalidated"
        assert Q.reprocess(dev_db, conflict.id) == "would_insert" and close_of(dev_db, co.id, D2) is None  # --apply 없이는 계획만
        assert Q.reprocess(dev_db, conflict.id, apply=True) == "reprocessed"
        assert close_of(dev_db, co.id, D2) == 40.0
        dev_db.refresh(conflict)
        assert conflict.resolution_status == "reprocessed" and conflict.retry_count == 2 and conflict.resolved_at is not None

    def test_reprocess_never_overwrites_existing(self, dev_db, fake_co):  # 21
        co = fake_co()
        rec = {"price_date": D4, "open_price": 55, "high_price": 55, "low_price": 55, "close_price": 55, "volume": 1, "change_pct": None}
        v = Verdict()
        v.reject("conflicts_existing", "합성")
        Q.record("stock_price", [Checked(rec, f"stock_price:{co.ticker}:{D4}", v)], {"ticker": co.ticker})
        item = q_rows(dev_db, co.ticker)[0]
        assert Q.revalidate(dev_db, item.id) == "revalidated"  # 지금은 같은 날 기존 값이 없다
        dev_db.add(StockPrice(company_id=co.id, price_date=D4, open_price=60, high_price=60, low_price=60, close_price=60, volume=1))
        dev_db.commit()  # 재처리 전에 정상 데이터가 들어옴(다음 수집)
        assert Q.reprocess(dev_db, item.id, apply=True) == "kept_existing"
        assert close_of(dev_db, co.id, D4) == 60.0
        dev_db.refresh(item)
        assert item.resolution_status == "kept_existing"


def naver_item(link, title="captest_q36 신공장 착공", pub="Thu, 05 Nov 2026 09:00:00 +0900"):
    return {"title": title, "description": "본문", "link": link, "originallink": link, "pubDate": pub}


class TestNewsDisclosureEtf:
    def test_news_bad_record_quarantined_good_saved(self, dev_db, fake_co, monkeypatch):  # 12·16
        co = fake_co()
        good = f"https://www.mk.co.kr/news/{secrets.token_hex(4)}"
        monkeypatch.setattr(collectors, "fetch_naver_news", lambda q, display=10, sort="date": [
            naver_item(good, f"{co.name} 신공장"), naver_item("", f"{co.name} 링크 없음"),
            naver_item(f"https://www.mk.co.kr/news/{secrets.token_hex(4)}", f"{co.name} 미래 기사", "Fri, 20 Nov 2026 09:00:00 +0900")])
        r = collectors.fetch_and_save_news(co.name, display=10, ticker=co.ticker)
        assert r.ok and r.saved == 1 and r.quarantined == 2
        assert [n.url for n in dev_db.query(News).filter(News.company_id == co.id)] == [good]
        assert sorted(x.error_code for x in dev_db.query(DataIngestionQuarantine).filter(
            DataIngestionQuarantine.source == "news", DataIngestionQuarantine.payload["ticker"].astext == co.ticker)) == \
            ["future_published", "url_missing"]

    def test_correction_disclosure_saved_bad_quarantined(self, dev_db, fake_co, monkeypatch):  # 10·11
        co = fake_co()
        serial = f"{secrets.randbelow(10**6):06d}"  # 접수번호 뒤 6자리(실제 공시와 겹치지 않게 임의)
        good, nextday = "20261105" + serial, "20261102" + serial  # nextday: 11-02 제출, 11-03(다음 영업일) 접수
        items = [
            {"rcept_no": good, "rcept_dt": "20261105", "report_nm": "[기재정정]주요사항보고서(자기주식취득결정)", "corp_name": co.name, "flr_nm": co.name},
            {"rcept_no": nextday, "rcept_dt": "20261103", "report_nm": "주요사항보고서(전환사채권발행결정)", "corp_name": co.name, "flr_nm": co.name},
            {"rcept_no": "12345", "rcept_dt": "20261105", "report_nm": "잘못된 접수번호", "corp_name": co.name, "flr_nm": co.name},
        ]
        monkeypatch.setattr(collectors, "get_dart_corp_code", lambda t: "00000000")
        monkeypatch.setattr(collectors, "fetch_dart_disclosures", lambda corp, days=90: items)
        r = collectors.fetch_and_save_disclosures(co.name)
        assert r.ok and r.saved == 2 and r.quarantined == 1 and r.warned == 1
        titles = sorted(d.title for d in dev_db.query(Disclosure).filter(Disclosure.company_id == co.id))
        assert titles == ["[기재정정]주요사항보고서(자기주식취득결정)", "주요사항보고서(전환사채권발행결정)"]
        got = {(x.validation_status, x.error_code) for x in dev_db.query(DataIngestionQuarantine).filter(
            DataIngestionQuarantine.source == "disclosure", DataIngestionQuarantine.payload["ticker"].astext == co.ticker)}
        assert got == {("quarantined", "receipt_invalid"), ("warning", "receipt_next_business_day")}

    def test_bad_etf_snapshot_keeps_existing_holdings(self, dev_db, fake_co, monkeypatch):  # 9·15
        co = fake_co(name="TIGER captest_q36", asset_type="etf")
        good = E.Snapshot(source=E.TIGER, as_of=D4, holdings=[E.Holding("삼성전자", "005930", 60.0), E.Holding("SK하이닉스", "000660", 40.0)])
        from app.etf_holdings_db import save_snapshot

        save_snapshot(dev_db, co.id, good)
        broken = E.Snapshot(source=E.TIGER, as_of=D4, holdings=[E.Holding("삼성전자", "005930", 0.0), E.Holding("SK하이닉스", "000660", 0.0)])
        monkeypatch.setattr(EC, "fetch_tiger", lambda client, ticker: broken)
        res = EC.collect(dev_db, tickers=[co.ticker], client=EC.PoliteClient(0, get=lambda *a, **k: None, sleep=lambda s: None),
                         log=lambda m: None, fid_cache=None)
        assert res[0].quarantined and not res[0].ok and res[0].error.startswith("quality_quarantined")
        dev_db.expire_all()
        kept = sorted((h.holding_code, float(h.weight_pct)) for h in dev_db.query(EtfHolding).filter(EtfHolding.company_id == co.id))
        assert kept == [("000660", 40.0), ("005930", 60.0)]  # 같은 기준일의 기존 구성종목 그대로
        # 음수 비중이 섞인 정상 스냅샷은 경고와 함께 저장
        neg = E.Snapshot(source=E.TIGER, as_of=D4, holdings=[E.Holding("삼성전자", "005930", 105.0), E.Holding("선물", None, -5.0)])
        monkeypatch.setattr(EC, "fetch_tiger", lambda client, ticker: neg)
        res = EC.collect(dev_db, tickers=[co.ticker], client=EC.PoliteClient(0, get=lambda *a, **k: None, sleep=lambda s: None),
                         log=lambda m: None, fid_cache=None)
        assert res[0].ok and not res[0].quarantined
        dev_db.expire_all()
        assert dev_db.query(EtfHolding).filter(EtfHolding.company_id == co.id).count() == 2


class TestRunContextAndOps:
    def test_counts_for_run_and_report(self, dev_db, fake_co):
        from app import data_monitor

        co = fake_co()
        run = DataRefreshRun(status="running", trigger="manual", steps="prices")
        dev_db.add(run)
        dev_db.commit()
        try:
            Q.set_context(run.id, "prices")
            v = Verdict()
            v.reject("close_nonpositive", "합성")
            w = Verdict()
            w.warn("extreme_move", "합성")
            rec = lambda d, c: {"price_date": d, "close_price": c, "open_price": c, "high_price": c, "low_price": c, "volume": 1}
            Q.record("stock_price", [Checked(rec(D1, 0), f"stock_price:{co.ticker}:{D1}", v),
                                     Checked(rec(D2, 140), f"stock_price:{co.ticker}:{D2}", w)], {"ticker": co.ticker})
        finally:
            Q.clear_context()
        assert Q.counts_for_run(dev_db, run.id, "prices") == {"quarantined": 1, "warning": 1}
        summary = data_monitor.ingestion_summary(dev_db)
        assert summary["available"] and summary["sources"]["stock_price"]["by_code"].get("close_nonpositive", 0) >= 1
        dev_db.execute(text("DELETE FROM data_ingestion_quarantine WHERE run_id = :r"), {"r": run.id})
        dev_db.query(DataRefreshRun).filter(DataRefreshRun.id == run.id).delete()
        dev_db.commit()

    def test_cli_and_prune(self, dev_db, fake_co, capsys):  # 22
        from app.db.session import SessionLocal
        from scripts import quarantine as cli

        co = fake_co()
        v = Verdict()
        v.reject("close_nonpositive", "합성")
        w = Verdict()
        w.warn("extreme_move", "합성")
        rec = lambda d, c: {"price_date": d, "close_price": c, "open_price": c, "high_price": c, "low_price": c, "volume": 1}
        Q.record("stock_price", [Checked(rec(D1, 0), f"stock_price:{co.ticker}:{D1}", v),
                                 Checked(rec(D2, 140), f"stock_price:{co.ticker}:{D2}", w),
                                 Checked(rec(D3, -1), f"stock_price:{co.ticker}:{D3}", v)], {"ticker": co.ticker})
        rows = {x.source_key[-10:]: x for x in q_rows(dev_db, co.ticker)}
        assert cli.main(["show", str(rows[str(D1)].id)], session_factory=SessionLocal) == 0
        out = capsys.readouterr().out
        assert "close_nonpositive" in out and co.ticker in out
        assert cli.main(["discard", str(rows[str(D3)].id), "--note", "captest"], session_factory=SessionLocal) == 0
        dev_db.execute(text("UPDATE data_ingestion_quarantine SET last_seen_at = now() - interval '400 days' WHERE source_key LIKE :k"),
                       {"k": f"%:{co.ticker}:%"})
        dev_db.commit()
        prefix = f"stock_price:{co.ticker}:"
        assert Q.prune(dev_db, 180, dry_run=True, key_prefix=prefix) == 2  # 경고 + 버린 것(미해결 격리는 남김)
        assert Q.prune(dev_db, 180, key_prefix=prefix) == 2
        left = q_rows(dev_db, co.ticker)
        assert [(x.error_code, x.resolution_status) for x in left] == [("close_nonpositive", "open")]
        assert Q.prune(dev_db, 180, include_open=True, key_prefix=prefix) == 1 and q_rows(dev_db, co.ticker) == []
        with pytest.raises(ValueError):
            Q.prune(dev_db, 7, key_prefix=prefix)


class TestMigrationRoundTrip:
    def test_quarantine_upgrade_downgrade_upgrade(self):
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

        def state():
            with psycopg.connect(target_pg) as c:
                cols = [r[0] for r in c.execute("SELECT column_name FROM information_schema.columns "
                                                "WHERE table_name = 'data_ingestion_quarantine'").fetchall()]
                ver = c.execute("SELECT version_num FROM alembic_version").fetchone()[0]
            return cols, ver

        with psycopg.connect(admin, autocommit=True) as c:
            c.execute(f'CREATE DATABASE "{name}"')
        try:
            alembic("upgrade", "b8d4f2a6c913")
            assert state() == ([], "b8d4f2a6c913")
            alembic("upgrade", "c7e2a9f4d158")
            cols, ver = state()
            assert ver == "c7e2a9f4d158" and {"source", "source_key", "payload", "payload_hash", "resolution_status"} <= set(cols)
            with psycopg.connect(target_pg, autocommit=True) as c:  # 고유 키: 같은 (source, key, code, hash)는 한 행
                ins = ("INSERT INTO data_ingestion_quarantine (source, source_key, validation_status, error_code, error_detail, payload, "
                       "payload_hash) VALUES ('stock_price', 'k', 'quarantined', 'c', 'd', '{}', 'h')")
                c.execute(ins)
                with pytest.raises(psycopg.errors.UniqueViolation):
                    c.execute(ins)
            alembic("downgrade", "-1")
            assert state() == ([], "b8d4f2a6c913")
            alembic("upgrade", "c7e2a9f4d158")
            assert state()[1] == "c7e2a9f4d158"
        finally:
            with psycopg.connect(admin, autocommit=True) as c:
                c.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
