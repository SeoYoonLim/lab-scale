"""53차: stock_price_session 대기 마이그레이션(alembic/pending)과 적재 도구(scripts/load_session_minute.py).

- 단위: 대상 DB 보호(개발·보존 DB 거부), 버전 위치(versions + pending), 모델·마이그레이션 일치, 충돌 기록.
- 통합(integration): MIGRATION_TEST_DATABASE_URL이 가리키는 **별도 서버의 임시 DB**에서만 upgrade -> 적재 -> 재적재 ->
  충돌 -> UPDATE 거부 -> downgrade -> upgrade -> 재적재를 한다(dev DB 서버는 test_auth_revoke_devdb._migration_server_url이 거부).
"""
import json
import os
import secrets
import subprocess
import sys
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from app import krx_calendar
from scripts import alembic_pending as ap
from scripts import load_session_minute as lsm

BACKEND = Path(__file__).resolve().parents[1]
REVISION, BASE = "0f6d0ec52ac3", "c7e2a9f4d158"
KST = krx_calendar.KST


# ---------- 단위 ----------

@pytest.mark.parametrize("url", [
    "postgresql+psycopg://postgres:postgres@localhost:15432/ai_investment",
    "postgresql+psycopg://postgres:postgres@127.0.0.1:15432/ai_investment",
    "postgresql+psycopg://u:p@otherhost:5432/ai_investment",  # 이름이 개발 DB면 서버가 달라도 거부
    "postgresql+psycopg://postgres:postgres@127.0.0.1:15432/ai_investment_postcheck47",
    "postgresql+psycopg://postgres:postgres@127.0.0.1:15432/ai_investment_verify",
    "postgresql+psycopg://postgres:postgres@127.0.0.1:15432/postgres",
    "postgresql+psycopg://postgres:postgres@127.0.0.1:15432/",
    "postgresql+psycopg://postgres:postgres@127.0.0.1:15432/some_other_db",  # 54차: 허용 이름(ai_investment_session_*, captest_*)만
])
def test_check_target_refuses_dev_and_preserved_databases(url):
    with pytest.raises(ap.Refused):
        ap.check_target(url)


def test_check_target_allows_test_database_and_dev_only_with_flag():
    assert ap.check_target("postgresql+psycopg://postgres:postgres@127.0.0.1:15432/ai_investment_session_design53").database \
        == "ai_investment_session_design53"
    assert ap.check_target("postgresql+psycopg://postgres:postgres@localhost:15432/ai_investment", allow_dev_db=True)
    with pytest.raises(ap.Refused):  # 보존 DB는 플래그로도 안 된다
        ap.check_target("postgresql+psycopg://postgres:postgres@localhost:15432/ai_investment_postcheck47", allow_dev_db=True)


def test_cli_refuses_dev_db_before_touching_alembic(capsys):
    assert ap.main(["--database-url", "postgresql+psycopg://postgres:postgres@localhost:15432/ai_investment", "upgrade", REVISION]) == 2
    assert "개발 DB" in capsys.readouterr().err
    assert ap.main(["--database-url", "postgresql+psycopg://x:y@127.0.0.1:1/ai_investment_session_design53", "upgrade"]) == 2


def test_pending_revision_is_not_in_default_alembic_head():
    """기본 alembic.ini(=health 검사·일반 upgrade head)는 pending을 모른다. pending을 더하면 head가 0f6d0ec52ac3 하나다."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    default = ScriptDirectory.from_config(Config(str(BACKEND / "alembic.ini")))
    assert default.get_heads() == [BASE]
    pending = ScriptDirectory.from_config(ap.alembic_config())
    assert pending.get_heads() == [REVISION]
    assert pending.get_revision(REVISION).down_revision == BASE


def test_model_matches_migration_columns():
    """모델(app/models/stock_price_session.py)과 대기 마이그레이션의 컬럼 이름·NULL 허용이 같다. 모델은 아직 등록하지 않는다."""
    import importlib.util

    import app.models as models
    from app.models.stock_price_session import StockPriceSession

    spec = importlib.util.spec_from_file_location("m53", BACKEND / "alembic" / "pending" / f"{REVISION}_add_stock_price_session.py")
    mod = importlib.util.module_from_spec(spec)

    captured = {}

    class FakeOp:
        def create_table(self, name, *cols):
            captured["cols"] = {c.name: c.nullable for c in cols if hasattr(c, "nullable") and hasattr(c, "type")}

        def __getattr__(self, _):
            return lambda *a, **k: None

    import alembic

    real = getattr(alembic, "op", None)
    spec.loader.exec_module(mod)
    mod.op = FakeOp()
    mod.upgrade()
    if real is not None:
        alembic.op = real
    model_cols = {c.name: c.nullable for c in StockPriceSession.__table__.columns}
    assert captured["cols"] == model_cols
    assert "StockPriceSession" not in getattr(models, "__all__", [])  # 승인 전 미등록(autogenerate 방지)


def test_conflict_record_shows_both_values_in_kst():
    at = datetime(2026, 11, 12, 9, 0, tzinfo=KST)
    old = {"regular": (Decimal("1"), Decimal("2"), Decimal("1"), Decimal("2"), 10, 1, at, at)}
    new = {"regular": (Decimal("1"), Decimal("2"), Decimal("1"), Decimal("2"), 11, 1, at, at), "after_market": old["regular"]}
    d = lsm.Loader._diff("005930", date(2026, 11, 12), old, new, "conflict")
    assert d["sessions"]["regular"]["db"]["volume"] == 10 and d["sessions"]["regular"]["source"]["volume"] == 11
    assert d["sessions"]["after_market"]["db"] is None
    assert d["sessions"]["regular"]["db"]["first_bar_at"] == "2026-11-12T09:00:00+09:00"


def test_loader_refuses_dev_db_and_missing_raw(tmp_path, capsys):
    (tmp_path / "raw").mkdir()
    assert lsm.main(["--database-url", "postgresql+psycopg://postgres:postgres@localhost:15432/ai_investment",
                     "--source", str(tmp_path), "--date", "2026-11-12"]) == 2
    assert lsm.main(["--database-url", "postgresql+psycopg://p:p@127.0.0.1:1/ai_investment_session_design53",
                     "--source", str(tmp_path / "nope"), "--date", "2026-11-12"]) == 2
    assert lsm.main(["--database-url", "postgresql+psycopg://p:p@127.0.0.1:1/ai_investment_session_design53",
                     "--source", str(tmp_path), "--date", "2026-11-15"]) == 2  # 일요일
    assert "거부" in capsys.readouterr().err


def test_load_requires_confirm_database_report_and_verified_schedule(tmp_path, capsys):
    """54차: 적재는 DB 이름 재확인·출처 기록이 필요하고, 발표만 된 일정(2026-11-19)은 별도 허용 없이는 적재하지 않는다. DB 연결 전 거부."""
    (tmp_path / "raw").mkdir()
    url = "postgresql+psycopg://p:p@127.0.0.1:1/ai_investment_session_x"
    base = ["--database-url", url, "--source", str(tmp_path)]
    assert lsm.main(base + ["--date", "2026-11-12", "--load", "--report", str(tmp_path.parent / "r.json")]) == 2
    assert lsm.main(base + ["--date", "2026-11-12", "--load", "--confirm-database", "ai_investment_session_y",
                            "--report", str(tmp_path.parent / "r.json")]) == 2
    assert lsm.main(base + ["--date", "2026-11-12", "--load", "--confirm-database", "ai_investment_session_x"]) == 2
    assert lsm.main(base + ["--date", "2026-11-19", "--load", "--confirm-database", "ai_investment_session_x",
                            "--report", str(tmp_path.parent / "r.json")]) == 2
    err = capsys.readouterr().err
    assert "--confirm-database" in err and "--report" in err and "announced" in err


# ---------- 통합: 별도 서버의 임시 DB ----------

def bars(day="20261112", auction="1530", close=208000):
    def r(hm, o, h=None, lo=None, c=None, v=100):
        return {"localDateTime": f"{day}{hm}00", "openPrice": o, "highPrice": h or o, "lowPrice": lo or o, "currentPrice": c or o,
                "accumulatedTradingVolume": v}
    return [r("0900", 208500, 209000, 208000, 208500, 1000), r("1519", 207000, 207500, 206000, 207000, 3000),
            r(auction, close, v=5000), r("1540", close, v=3), r("1600", 208000, 209000, 207500, 208500, 300), r("1959", 206500, v=30)]


def write_source(root: Path, files: dict) -> Path:
    (root / "raw").mkdir(parents=True)
    for t, rows in files.items():
        (root / "raw" / f"{t}.json").write_text(json.dumps(rows), encoding="utf-8")
    at = "2026-11-12T21:00:00+09:00"
    (root / "fetch_log.json").write_text(json.dumps({"started_at": at, "fetched_at": {t: at for t in files}}), encoding="utf-8")
    return root


@pytest.mark.integration
class TestMigrationAndLoadRoundTrip:
    def _run(self, *args, env_url=None):
        env = {**os.environ, **({"DATABASE_URL": env_url} if env_url else {})}
        r = subprocess.run([sys.executable, *args], cwd=BACKEND, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
        return r

    def test_upgrade_load_conflict_downgrade_upgrade(self, tmp_path):
        import psycopg

        from test_auth_revoke_devdb import _migration_server_url

        server = _migration_server_url()
        name = f"captest_sps_{secrets.token_hex(4)}"
        admin = server.set(drivername="postgresql").render_as_string(hide_password=False)
        target = server.set(database=name)
        sa_url = target.render_as_string(hide_password=False)
        pg_url = target.set(drivername="postgresql").render_as_string(hide_password=False)
        with psycopg.connect(admin, autocommit=True) as c:
            c.execute(f'CREATE DATABASE "{name}"')
        try:
            assert self._run("-m", "alembic", "upgrade", "head", env_url=sa_url).returncode == 0  # 기본 head = c7e2a9f4d158
            with psycopg.connect(pg_url, autocommit=True) as c:
                assert c.execute("SELECT version_num FROM alembic_version").fetchone()[0] == BASE
                assert c.execute("SELECT to_regclass('stock_price_session')").fetchone()[0] is None
                c.execute("INSERT INTO company (ticker, name, asset_type) VALUES ('005930', 's', 'stock'), ('385540', 'e', 'etf')")
                c.execute("INSERT INTO stock_price (company_id, price_date, close_price) "
                          "SELECT id, '2026-11-12', 208000 FROM company WHERE ticker = '005930'")
                before_sp = c.execute("SELECT count(*), md5(string_agg(t::text, ',' order by id)) FROM stock_price t").fetchone()

            r = self._run("-m", "scripts.alembic_pending", "--database-url", sa_url, "upgrade", REVISION)
            assert r.returncode == 0, r.stderr[-1500:]
            src = write_source(tmp_path / "src", {"005930": bars(auction="1532"),
                                                   "385540": [bars()[0], {**bars()[1], "localDateTime": "20261112151800"}]})
            args = ["--database-url", sa_url, "--source", str(src), "--date", "2026-11-12"]
            load = ["--load", "--confirm-database", name, "--report", str(tmp_path / "rl.json")]
            for mode in ([], ["--validate-only"], ["--dry-run"]):  # 54차: 기본·검증·미리 보기는 쓰지 않는다
                assert lsm.main(args + mode) == 0
            with psycopg.connect(pg_url) as c:
                assert c.execute("SELECT count(*) FROM stock_price_session").fetchone()[0] == 0
            assert lsm.main(args + ["--load", "--confirm-database", name, "--report", str(tmp_path / "r1.json")]) == 0
            r1 = json.loads((tmp_path / "r1.json").read_text(encoding="utf-8"))
            day = next(d for d in r1["days"] if d["ticker"] == "005930")  # 출처 기록
            assert day["file"] == "raw/005930.json" and len(day["sha256"]) == 64 and day["result"] == "inserted"
            assert day["schedule"]["status"] == "data_verified" and day["aggregated_at"] and set(day["sessions"]) == {
                "regular", "closing_auction", "post_close", "after_market"}
            assert r1["counts"]["inserted_rows"] == 5 and r1["official_close"] == {"match": 1, "no_official_close": 1}
            assert r1["flags"] == {"etf:no_closing_auction": 1, "stock:late_closing_auction": 1}

            def table():
                with psycopg.connect(pg_url) as c:
                    return c.execute("SELECT count(*), md5(string_agg(t::text, ',' order by id)) FROM stock_price_session t").fetchone()

            with psycopg.connect(pg_url) as c:
                rows = dict(c.execute("SELECT session, to_char(first_bar_at AT TIME ZONE 'Asia/Seoul', 'HH24:MI') FROM stock_price_session s "
                                      "JOIN company co ON co.id = s.company_id WHERE ticker = '005930'").fetchall())
            assert rows == {"regular": "09:00", "closing_auction": "15:32", "post_close": "15:40", "after_market": "16:00"}
            first = table()
            assert lsm.main(args + load) == 0 and table() == first  # 재적재: 그대로
            assert lsm.main(args + ["--verify-only"]) == 0

            bad = write_source(tmp_path / "bad", {"005930": [dict(b, accumulatedTradingVolume=b["accumulatedTradingVolume"] + 1)
                                                             if b["localDateTime"].endswith("090000") else b for b in bars(auction="1532")]})
            assert lsm.main(["--database-url", sa_url, "--source", str(bad), "--date", "2026-11-12",
                             "--load", "--confirm-database", name, "--report", str(tmp_path / "r2.json")]) == 1
            conflict = json.loads((tmp_path / "r2.json").read_text(encoding="utf-8"))["conflicts"][0]
            assert conflict["sessions"]["regular"]["db"]["volume"] == 4000 and conflict["sessions"]["regular"]["source"]["volume"] == 4001
            assert table() == first  # 충돌은 아무것도 바꾸지 않는다

            with psycopg.connect(pg_url, autocommit=True) as c:
                with pytest.raises(psycopg.errors.CheckViolation):
                    c.execute("UPDATE stock_price_session SET volume = volume + 1")
                with pytest.raises(psycopg.errors.CheckViolation):
                    c.execute("INSERT INTO stock_price_session (company_id, price_date, venue, session, open_price, high_price, low_price,"
                              " close_price, volume, bar_count, first_bar_at, last_bar_at, source, source_file_sha256, rules_version,"
                              " price_basis_date, collected_at) SELECT id, '2026-11-13', 'NXT', 'after_market', 1, 1, 1, 1, 1, 1,"
                              " '2026-11-13 16:00+09', '2026-11-13 16:00+09', 'x', repeat('a', 64), 1, '2026-11-14', '2026-11-14 09:00+09'"
                              " FROM company WHERE ticker = '005930'")
            assert table() == first

            r = self._run("-m", "scripts.alembic_pending", "--database-url", sa_url, "downgrade", BASE)
            assert r.returncode == 0, r.stderr[-1500:]
            with psycopg.connect(pg_url) as c:
                assert c.execute("SELECT to_regclass('stock_price_session')").fetchone()[0] is None
                assert c.execute("SELECT count(*) FROM pg_proc WHERE proname = 'stock_price_session_no_update'").fetchone()[0] == 0
                assert c.execute("SELECT version_num FROM alembic_version").fetchone()[0] == BASE
                assert c.execute("SELECT count(*), md5(string_agg(t::text, ',' order by id)) FROM stock_price t").fetchone() == before_sp
            assert lsm.main(args + ["--dry-run"]) == 2  # 표가 없으면 거부

            assert self._run("-m", "scripts.alembic_pending", "--database-url", sa_url, "upgrade", REVISION).returncode == 0
            assert lsm.main(args + load) == 0 and table()[0] == 5

            # 54차: 한 종목-날짜의 충돌이 같은 실행의 다른 종목을 막지 않는다
            with psycopg.connect(pg_url, autocommit=True) as c:
                c.execute("INSERT INTO company (ticker, name, asset_type) VALUES ('000660', 'h', 'stock')")
            mixed = write_source(tmp_path / "mixed", {
                "005930": [dict(b, accumulatedTradingVolume=b["accumulatedTradingVolume"] + 1) if b["localDateTime"].endswith("090000")
                           else b for b in bars(auction="1532")],
                "000660": bars()})
            assert lsm.main(["--database-url", sa_url, "--source", str(mixed), "--date", "2026-11-12", "--load",
                             "--confirm-database", name, "--report", str(tmp_path / "r3.json")]) == 1
            r3 = json.loads((tmp_path / "r3.json").read_text(encoding="utf-8"))
            assert {d["ticker"]: d["result"] for d in r3["days"]} == {"000660": "inserted", "005930": "conflict"}
            assert table()[0] == 9  # 000660 4행만 더해짐, 005930은 그대로

            # 54차: 수능일 합성 분봉(시험용 임시 DB에서만). 발표 일정이라 허용 플래그 없이는 적재 거부, 있으면 DB 제약을 통과
            csat = write_source(tmp_path / "csat", {"000660": [
                {**b, "localDateTime": "20261119" + hm + "00"} for b, hm in zip(bars(), ("1000", "1619", "1630", "1640", "1700", "1959"))]})
            fetch = json.loads((csat / "fetch_log.json").read_text(encoding="utf-8"))
            fetch["fetched_at"] = {"000660": "2026-11-19T21:00:00+09:00"}
            (csat / "fetch_log.json").write_text(json.dumps(fetch), encoding="utf-8")
            ca = ["--database-url", sa_url, "--source", str(csat), "--date", "2026-11-19", "--load", "--confirm-database", name,
                  "--report", str(tmp_path / "r4.json")]
            assert lsm.main(ca) == 2
            assert lsm.main(ca + ["--allow-unverified-schedule"]) == 0
            with psycopg.connect(pg_url) as c:
                got = dict(c.execute("SELECT session, to_char(first_bar_at AT TIME ZONE 'Asia/Seoul', 'HH24:MI') FROM stock_price_session "
                                     "WHERE price_date = '2026-11-19'").fetchall())
            assert got == {"regular": "10:00", "closing_auction": "16:30", "post_close": "16:40", "after_market": "17:00"}
        finally:
            with psycopg.connect(admin, autocommit=True) as c:
                c.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
