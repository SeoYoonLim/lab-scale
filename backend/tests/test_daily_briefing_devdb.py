"""일일 투자 브리핑 API를 실제 DB로 검증한다: 인증, 사용자 분리, 저장·날짜별 이력, 같은 날 중복 생성 방지, 다시 만들기(버전 보존),
과거 스냅샷 보존, 하루 버전 상한, 언어별 저장, 숫자의 독립 재계산 대조.

테스트 계정은 captest_ + 임의값이다(signup 픽스처가 끝나면 계정·관심종목·모의투자 계좌를 지우고, 브리핑은 users FK CASCADE로 지워진다).
시세·뉴스는 읽기만 한다. CI의 빈 DB(시세 없음)에서도 통과하도록 썼다: 시세가 없으면 브리핑은 no_data 상태이고 숫자는 null이다.
LLM은 부르지 않는다(use_llm=false, 또는 daily_briefing_db._chat을 가짜로 바꾼다).
"""

import json
import secrets
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app import daily_briefing_db as store
from app.main import app
from app.models import DailyBriefing, Holding, VirtualAccount, Watchlist

client = TestClient(app, raise_server_exceptions=False)
BASE = "/api/briefings/daily"


@pytest.fixture
def captest(signup):
    def _make():
        return signup(f"captest_{secrets.token_hex(5)}")

    return _make


@pytest.fixture(autouse=True)
def no_llm(monkeypatch):
    def down(messages):
        raise ConnectionError("테스트에서는 Ollama에 연결하지 않는다")

    monkeypatch.setattr(store, "_chat", down)


def key(user) -> str:
    return f"user:{user['id']}"


def seed(dev_db, user, holding_ids, watch_ids, cash=5_000_000):
    dev_db.add(VirtualAccount(device_id=key(user), cash_balance=cash))
    dev_db.flush()
    for cid in holding_ids:
        dev_db.add(Holding(device_id=key(user), company_id=cid, quantity=3, avg_price=10_000))
    for cid in watch_ids:
        dev_db.add(Watchlist(device_id=key(user), company_id=cid))
    dev_db.commit()


def stock_ids(dev_db, n):
    return [i for (i,) in dev_db.execute(text("SELECT id FROM company WHERE asset_type = 'stock' ORDER BY id LIMIT :n"), {"n": n})]


def gen(user, **body):
    return client.post(f"{BASE}/generate", json={"use_llm": False, **body}, headers=user["headers"])


@pytest.mark.integration
class TestDailyBriefingDevDB:
    @pytest.mark.parametrize("method, path", [("POST", f"{BASE}/generate"), ("GET", f"{BASE}/current"), ("GET", f"{BASE}/history"),
                                              ("GET", f"{BASE}/1")])
    def test_requires_login(self, method, path):
        assert client.request(method, path).status_code == 401

    def test_generate_once_per_day_then_current_and_history(self, captest, dev_db):
        user = captest()
        assert client.get(f"{BASE}/current", headers=user["headers"]).status_code == 404
        r1 = gen(user)
        assert r1.status_code == 201, r1.text
        b1 = r1.json()
        assert b1["created"] is True and b1["version"] == 1 and b1["locale"] == "ko-KR" and b1["timezone"] == "Asia/Seoul"
        assert b1["content"]["section_order"] == ["market", "holdings", "watchlist", "risk", "checklist"]
        assert set(b1["content"]["sections"]) == set(b1["content"]["section_order"])
        assert b1["summary_source"] == "code" and b1["original_content"] is None
        # 같은 날 다시 부르면 새로 만들지 않고 저장본을 그대로(200, created=false)
        r2 = gen(user)
        assert r2.status_code == 200 and r2.json()["created"] is False and r2.json()["id"] == b1["id"]
        assert r2.json()["content"] == b1["content"]
        dev_db.rollback()
        assert dev_db.query(DailyBriefing).filter(DailyBriefing.user_id == user["id"]).count() == 1
        cur = client.get(f"{BASE}/current", headers=user["headers"]).json()
        assert cur["id"] == b1["id"] and "created" not in cur or cur.get("created") is None
        hist = client.get(f"{BASE}/history", headers=user["headers"]).json()
        assert hist["total"] == 1 and hist["items"][0]["id"] == b1["id"] and hist["items"][0]["versions_on_date"] == 1
        assert hist["items"][0]["detail_path"] == f"{BASE}/{b1['id']}" and hist["items"][0]["date_group"] == "today"

    def test_regenerate_keeps_old_version_and_snapshot_is_not_recalculated(self, captest, dev_db):
        user = captest()
        ids = stock_ids(dev_db, 3)
        seed(dev_db, user, ids[:1], ids[1:3])
        first = gen(user).json()
        assert {t["scope"] for t in first["tickers"]} == {"holding", "watchlist"} and len(first["tickers"]) == 3
        # 보유를 바꾼 뒤에도 과거 브리핑은 저장 당시 그대로다(현재 데이터로 다시 계산하지 않는다)
        dev_db.query(Holding).filter(Holding.device_id == key(user)).update({"quantity": 99})
        dev_db.commit()
        again = client.get(f"{BASE}/{first['id']}", headers=user["headers"]).json()
        assert again["content"] == first["content"] and again["tickers"] == first["tickers"]
        assert again["content"]["sections"]["holdings"]["items"][0]["quantity"] == 3
        second = gen(user, regenerate=True)
        assert second.status_code == 201 and second.json()["version"] == 2
        assert second.json()["content"]["sections"]["holdings"]["items"][0]["quantity"] == 99
        old = client.get(f"{BASE}/{first['id']}", headers=user["headers"]).json()
        assert old["version"] == 1 and old["content"] == first["content"]
        cur = client.get(f"{BASE}/current", headers=user["headers"]).json()
        assert cur["version"] == 2
        latest = client.get(f"{BASE}/history", headers=user["headers"]).json()
        assert latest["total"] == 1 and latest["items"][0]["version"] == 2 and latest["items"][0]["versions_on_date"] == 2
        allv = client.get(f"{BASE}/history", params={"latest_only": "false"}, headers=user["headers"]).json()
        assert [i["version"] for i in allv["items"]] == [2, 1]

    def test_version_limit_is_409(self, captest):
        user = captest()
        assert gen(user).status_code == 201
        for _ in range(store.MAX_VERSIONS - 1):
            assert gen(user, regenerate=True).status_code == 201
        r = gen(user, regenerate=True)
        assert r.status_code == 409 and str(store.MAX_VERSIONS) in r.json()["detail"]

    def test_users_are_isolated(self, captest, dev_db):
        a, b = captest(), captest()
        ids = stock_ids(dev_db, 4)
        seed(dev_db, b, ids[:2], ids[2:4])
        ba = gen(a).json()
        bb = gen(b).json()
        assert ba["tickers"] == [] and {t["ticker"] for t in bb["tickers"]} == {
            t for (t,) in dev_db.execute(text("SELECT ticker FROM company WHERE id = ANY(:ids)"), {"ids": ids})}
        dumped = json.dumps(ba, ensure_ascii=False)
        assert all(t["ticker"] not in dumped for t in bb["tickers"])
        # 남의 브리핑: 상세 404, 목록에 없음, current에도 안 보인다
        assert client.get(f"{BASE}/{bb['id']}", headers=a["headers"]).status_code == 404
        assert client.get(f"{BASE}/{ba['id']}", headers=b["headers"]).status_code == 404
        assert [i["id"] for i in client.get(f"{BASE}/history", headers=a["headers"]).json()["items"]] == [ba["id"]]
        assert client.get(f"{BASE}/current", headers=a["headers"]).json()["id"] == ba["id"]

    def test_holdings_and_watchlist_overlap_is_shown_once(self, captest, dev_db):
        user = captest()
        ids = stock_ids(dev_db, 2)
        seed(dev_db, user, ids[:1], ids[:2])  # 첫 종목은 보유이면서 관심
        body = gen(user).json()
        held = [t for t in body["tickers"] if t["scope"] == "holding"]
        watch = [t for t in body["tickers"] if t["scope"] == "watchlist"]
        assert len(held) == 1 and len(watch) == 1 and held[0]["ticker"] != watch[0]["ticker"]
        w = body["content"]["sections"]["watchlist"]
        assert w["dedup_text"] == "보유 종목과 겹치는 관심 종목 1개는 보유 종목 영역에만 표시했습니다."

    def test_empty_account_has_explicit_empty_sections(self, captest, dev_db):
        user = captest()
        c = gen(user).json()["content"]
        s = c["sections"]
        assert s["holdings"]["status"] == "empty" and s["watchlist"]["status"] == "empty" and s["risk"]["status"] == "empty"
        assert c["market_data_status"] in ("latest", "delayed", "stale", "no_data")
        # 브리핑을 만들어도 모의투자 계좌는 생기지 않는다(읽기 전용 입력)
        dev_db.rollback()
        assert dev_db.execute(text("SELECT count(*) FROM virtual_account WHERE device_id = :k"), {"k": key(user)}).scalar() == 0

    @pytest.mark.parametrize("locale", ["en-US", "ja-JP", "zh-CN"])
    def test_locale_is_stored_with_korean_original(self, captest, dev_db, locale):
        user = captest()
        ids = stock_ids(dev_db, 2)
        seed(dev_db, user, ids[:1], ids[1:2])
        body = gen(user, locale=locale).json()
        assert body["locale"] == locale and body["content"]["locale"] == locale and body["original_content"]["locale"] == "ko-KR"
        a, o = body["content"]["sections"]["holdings"]["items"][0], body["original_content"]["sections"]["holdings"]["items"][0]
        assert (a["ticker"], a["name"], a["latest_close"], a["eval_amount"], a["weight_pct"]) == (o["ticker"], o["name"], o["latest_close"],
                                                                                                    o["eval_amount"], o["weight_pct"])
        assert body["content"]["sections"]["market"]["title"] != body["original_content"]["sections"]["market"]["title"]
        # 계정 언어 설정을 따른다(요청에 locale이 없을 때). 저장된 과거 기록의 언어는 바뀌지 않는다
        assert client.put("/api/auth/preferences", json={"preferred_locale": "ko-KR"}, headers=user["headers"]).status_code == 200
        assert client.get(f"{BASE}/{body['id']}", headers=user["headers"]).json()["locale"] == locale

    def test_account_locale_is_default(self, captest):
        user = captest()
        assert client.put("/api/auth/preferences", json={"preferred_locale": "ja-JP"}, headers=user["headers"]).status_code == 200
        assert gen(user).json()["content"]["title"].endswith("デイリー投資ブリーフィング")

    def test_numbers_match_an_independent_recalculation(self, captest, dev_db):
        """보유 종목의 기준일·종가·평가액을 SQL로 따로 구해 대조한다(시세가 없으면 null인지 확인)."""
        user = captest()
        ids = stock_ids(dev_db, 2)
        seed(dev_db, user, ids, [])
        body = gen(user).json()
        for it in body["content"]["sections"]["holdings"]["items"]:
            row = dev_db.execute(text(
                "SELECT p.price_date, p.close_price FROM stock_price p JOIN company c ON c.id = p.company_id "
                "WHERE c.ticker = :t AND (p.price_date < (now() AT TIME ZONE 'Asia/Seoul')::date "
                "OR (now() AT TIME ZONE 'Asia/Seoul')::time >= '15:30' OR extract(isodow FROM now() AT TIME ZONE 'Asia/Seoul') > 5) "
                "ORDER BY p.price_date DESC LIMIT 1"), {"t": it["ticker"]}).first()
            if row is None:
                assert it["latest_close"] is None and it["eval_amount"] is None
                continue
            assert it["latest_date"] == row[0].isoformat() and it["latest_close"] == float(row[1])
            assert it["eval_amount"] == round(float(row[1]) * 3, 2)
        assert body["data_as_of"] == body["content"]["data_as_of"]

    def test_llm_summary_is_stored_when_it_passes_checks(self, captest, monkeypatch):
        user = captest()
        def fake(messages):
            # 시세가 지연이면 첫 문장에 기준일이 있어야 통과한다(dev DB는 지연, CI의 빈 DB는 시세 없음)
            as_of = json.loads(messages[1]["content"])["data_as_of"]
            head = f"{as_of} 종가 기준입니다. " if as_of else ""
            return json.dumps({"summary": head + "보유 종목과 관심 종목이 없는 계정의 브리핑입니다."}, ensure_ascii=False)

        monkeypatch.setattr(store, "_chat", fake)
        body = gen(user, use_llm=True).json()
        assert body["summary_source"] == "llm" and body["content"]["summary"]["text"].endswith("보유 종목과 관심 종목이 없는 계정의 브리핑입니다.")

    def test_bad_timezone_is_422_and_timezone_is_kept(self, captest):
        user = captest()
        assert gen(user, timezone="Mars/Base").status_code == 422
        body = gen(user, timezone="America/New_York").json()
        assert body["timezone"] == "America/New_York"
        assert body["local_date"] == datetime.now(__import__("zoneinfo").ZoneInfo("America/New_York")).date().isoformat()


@pytest.mark.integration
class TestDailyBriefingMigration:
    """5e7b9d2c4a10 왕복(임시 DB). MIGRATION_TEST_DATABASE_URL이 없으면 건너뛴다(dev DB 서버에는 하지 않는다, CI는 설정돼 있다)."""

    REVISION, PREV = "5e7b9d2c4a10", "14a53dc73b9c"

    def _alembic(self, db_url, *args):
        import os
        import subprocess
        import sys

        from test_auth_revoke_devdb import BACKEND

        r = subprocess.run([sys.executable, "-m", "alembic", *args], cwd=BACKEND, env={**os.environ, "DATABASE_URL": db_url},
                           capture_output=True, text=True, timeout=180)
        assert r.returncode == 0, r.stderr[-2000:]

    def test_upgrade_downgrade_upgrade(self):
        import psycopg

        from test_auth_revoke_devdb import _migration_server_url

        server = _migration_server_url()
        name = f"captest_migr_{secrets.token_hex(4)}"
        admin = server.set(drivername="postgresql").render_as_string(hide_password=False)
        target = server.set(database=name)
        target_sa = target.render_as_string(hide_password=False)
        target_pg = target.set(drivername="postgresql").render_as_string(hide_password=False)

        def tables():
            with psycopg.connect(target_pg) as c:
                return [r[0] for r in c.execute("SELECT table_name FROM information_schema.tables WHERE table_name = 'daily_briefing'")]

        with psycopg.connect(admin, autocommit=True) as c:
            c.execute(f'CREATE DATABASE "{name}"')
        try:
            self._alembic(target_sa, "upgrade", self.PREV)
            assert tables() == []
            self._alembic(target_sa, "upgrade", self.REVISION)
            assert tables() == ["daily_briefing"]
            with psycopg.connect(target_pg, autocommit=True) as c:
                uid = c.execute("INSERT INTO users (username, password_hash) VALUES ('m1', 'x') RETURNING id").fetchone()[0]
                row = ("INSERT INTO daily_briefing (user_id, local_date, timezone, locale, version, generated_at, market_data_status, "
                       "summary_source, content, original_content, tickers, sources, section_status) "
                       "VALUES (%s, '2026-11-06', 'Asia/Seoul', %s, %s, now(), 'latest', 'code', '{}', '{}', '[]', '[]', '{}')")
                c.execute(row, (uid, "ko-KR", 1))
                with pytest.raises(psycopg.errors.UniqueViolation):  # 같은 사용자·날짜·버전은 한 건
                    c.execute(row, (uid, "ko-KR", 1))
                with pytest.raises(psycopg.errors.CheckViolation):
                    c.execute(row, (uid, "fr-FR", 2))
                c.execute("DELETE FROM users WHERE id = %s", (uid,))  # ON DELETE CASCADE
                assert c.execute("SELECT count(*) FROM daily_briefing").fetchone()[0] == 0
            self._alembic(target_sa, "downgrade", "-1")
            assert tables() == []
            self._alembic(target_sa, "upgrade", self.REVISION)
            assert tables() == ["daily_briefing"]
        finally:
            with psycopg.connect(admin, autocommit=True) as c:
                c.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
