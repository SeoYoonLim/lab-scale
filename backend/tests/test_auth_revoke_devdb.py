"""토큰 폐기를 실제 dev DB + 실제 토큰으로 검증한다(get_current_user를 override하지 않는다).

- 가입 → 로그인 → 로그아웃 → 이전 토큰 401 → 재로그인 성공
- 비밀번호 변경 후 이전 토큰 401, 새 토큰 200, 옛 비밀번호로 로그인 401
- 다른 사용자의 토큰은 영향받지 않음, tv 없는 옛 토큰의 하위 호환
- 마이그레이션 업그레이드/다운그레이드 왕복: MIGRATION_TEST_DATABASE_URL이 가리키는 별도 서버에 임시 DB를 만들어서 한다
  (dev DB에는 절대 하지 않는다). 로컬은 임시 컨테이너, CI는 backend-integration의 서비스 컨테이너를 쓴다.
테스트 계정은 captest_ + 임의값이고 끝나면 지운다.
"""

import os
import secrets
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.engine import make_url

from app.auth import ACCESS_TOKEN_TTL
from app.main import app
from app.models import User, VirtualAccount, Watchlist

client = TestClient(app, raise_server_exceptions=False)

pytestmark = pytest.mark.integration

BACKEND = Path(__file__).resolve().parents[1]
# 이 마이그레이션 한 단계만 검사한다("head"가 아니라 이 리비전까지 올린다: 뒤에 마이그레이션이 더 생겨도 이 테스트는 그대로다).
PREV_REVISION = "c5d8f3a07b14"
REVISION = "e2a9c4f71b03"


@pytest.fixture
def captest(dev_db):
    """실제 /api/auth/signup으로 captest_ 계정을 만들고 {"id","username","password","token"}을 돌려준다. 끝나면 지운다."""
    created = []

    def _signup():
        username = f"captest_{secrets.token_hex(6)}"
        password = secrets.token_urlsafe(12)
        r = client.post("/api/auth/signup", json={"username": username, "password": password})
        assert r.status_code == 201, r.status_code
        body = r.json()
        created.append(body["user"]["id"])
        return {**body["user"], "password": password, "token": body["access_token"]}

    yield _signup

    dev_db.rollback()
    for user_id in created:
        key = f"user:{user_id}"
        dev_db.query(Watchlist).filter(Watchlist.device_id == key).delete()
        dev_db.query(VirtualAccount).filter(VirtualAccount.device_id == key).delete()
        dev_db.query(User).filter(User.id == user_id).delete()
    dev_db.commit()


def h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def login(username: str, password: str):
    return client.post("/api/auth/login", json={"username": username, "password": password})


def token_version(dev_db, user_id: int) -> int:
    v = dev_db.execute(text("SELECT token_version FROM users WHERE id = :i"), {"i": user_id}).scalar()
    dev_db.rollback()
    return v


class TestLogout:
    def test_signup_login_logout_old_token_fails_relogin_works(self, captest, dev_db):
        u = captest()
        r = login(u["username"], u["password"])
        assert r.status_code == 200
        token = r.json()["access_token"]
        assert client.get("/api/auth/me", headers=h(token)).status_code == 200

        out = client.post("/api/auth/logout", headers=h(token))
        assert out.status_code == 200 and out.json() == {"ok": True}
        assert token_version(dev_db, u["id"]) == 1

        for method, path in [("GET", "/api/auth/me"), ("GET", "/api/watchlist"), ("GET", "/api/portfolio")]:
            assert client.request(method, path, headers=h(token)).status_code == 401, path
        assert client.get("/api/auth/me", headers=h(u["token"])).status_code == 401  # 가입 때 받은 토큰(다른 기기)도
        assert client.post("/api/auth/logout", headers=h(token)).status_code == 401  # 이미 무효
        assert token_version(dev_db, u["id"]) == 1

        again = login(u["username"], u["password"])
        assert again.status_code == 200
        assert client.get("/api/auth/me", headers=h(again.json()["access_token"])).json()["id"] == u["id"]

    def test_other_users_tokens_are_not_affected(self, captest, dev_db):
        a, b = captest(), captest()
        assert client.post("/api/auth/logout", headers=h(a["token"])).status_code == 200
        assert client.get("/api/auth/me", headers=h(b["token"])).status_code == 200
        assert token_version(dev_db, b["id"]) == 0

    def test_legacy_token_without_tv_is_rejected(self, captest, jwt_secret):
        """2026-10-30부터 tv는 필수다(tv 없이 발급된 토큰은 수명 24시간이 지나 남아 있지 않다)."""
        u = captest()
        now = datetime.now(timezone.utc)
        legacy = jwt.encode({"sub": str(u["id"]), "iat": now, "exp": now + ACCESS_TOKEN_TTL}, jwt_secret, algorithm="HS256")
        assert client.get("/api/auth/me", headers=h(legacy)).status_code == 401
        assert client.get("/api/auth/me", headers=h(u["token"])).status_code == 200


class TestChangePassword:
    def test_old_token_fails_new_token_works(self, captest, dev_db):
        u, other = captest(), captest()
        new_password = secrets.token_urlsafe(12)
        r = client.post(
            "/api/auth/change-password", headers=h(u["token"]),
            json={"current_password": u["password"], "new_password": new_password},
        )
        assert r.status_code == 200
        body = r.json()
        assert set(body) == {"user", "access_token", "token_type"}
        assert u["password"] not in r.text and new_password not in r.text

        assert client.get("/api/auth/me", headers=h(u["token"])).status_code == 401
        assert client.get("/api/auth/me", headers=h(body["access_token"])).json() == {
            "id": u["id"], "username": u["username"], "email": None, "email_verified": False}  # 이메일 필드는 2026-10-30 추가
        assert client.get("/api/watchlist", headers=h(body["access_token"])).status_code == 200
        assert token_version(dev_db, u["id"]) == 1

        assert login(u["username"], u["password"]).status_code == 401
        assert login(u["username"], new_password).status_code == 200
        stored = dev_db.execute(text("SELECT password_hash FROM users WHERE id = :i"), {"i": u["id"]}).scalar()
        dev_db.rollback()
        assert stored.startswith("$argon2id$") and new_password not in stored

        assert client.get("/api/auth/me", headers=h(other["token"])).status_code == 200

    def test_wrong_current_password_changes_nothing(self, captest, dev_db):
        u = captest()
        before = dev_db.execute(text("SELECT password_hash FROM users WHERE id = :i"), {"i": u["id"]}).scalar()
        dev_db.rollback()
        r = client.post(
            "/api/auth/change-password", headers=h(u["token"]),
            json={"current_password": "definitely-wrong-1", "new_password": secrets.token_urlsafe(12)},
        )
        assert r.status_code == 422
        after = dev_db.execute(text("SELECT password_hash FROM users WHERE id = :i"), {"i": u["id"]}).scalar()
        dev_db.rollback()
        assert after == before and token_version(dev_db, u["id"]) == 0
        assert client.get("/api/auth/me", headers=h(u["token"])).status_code == 200
        assert login(u["username"], u["password"]).status_code == 200


# ---------- 마이그레이션 왕복(별도 서버의 임시 DB) ----------


def _migration_server_url():
    raw = os.getenv("MIGRATION_TEST_DATABASE_URL")
    if not raw:
        pytest.skip("MIGRATION_TEST_DATABASE_URL이 없음(dev DB에서는 왕복하지 않는다. 임시 컨테이너 주소를 넣고 실행)")
    url = make_url(raw)
    dev = make_url(os.getenv("DATABASE_URL", "postgresql+psycopg://postgres:postgres@localhost:15432/ai_investment"))
    # dev DB 서버를 가리키면 거부한다(같은 서버의 다른 DB라도 쓰지 않는다)
    assert (url.host, url.port) != (dev.host, dev.port) and url.port != 15432, "dev DB 서버에는 마이그레이션 왕복을 하지 않습니다"
    return url


class TestMigrationRoundTrip:
    def _alembic(self, db_url: str, *args: str) -> None:
        env = {**os.environ, "DATABASE_URL": db_url}
        r = subprocess.run(
            [sys.executable, "-m", "alembic", *args], cwd=BACKEND, env=env, capture_output=True, text=True, timeout=180,
        )
        assert r.returncode == 0, r.stderr[-2000:]

    def test_upgrade_downgrade_upgrade(self):
        import psycopg

        server = _migration_server_url()
        name = f"captest_migr_{secrets.token_hex(4)}"
        admin = server.set(drivername="postgresql").render_as_string(hide_password=False)
        target = server.set(database=name)
        target_sa = target.render_as_string(hide_password=False)
        target_pg = target.set(drivername="postgresql").render_as_string(hide_password=False)

        with psycopg.connect(admin, autocommit=True) as c:
            c.execute(f'CREATE DATABASE "{name}"')
        try:
            self._alembic(target_sa, "upgrade", PREV_REVISION)
            with psycopg.connect(target_pg, autocommit=True) as c:
                c.execute("INSERT INTO users (username, password_hash) VALUES ('captest_old', 'x')")

            def column():
                with psycopg.connect(target_pg) as c:
                    return c.execute(
                        "SELECT is_nullable, column_default FROM information_schema.columns "
                        "WHERE table_name = 'users' AND column_name = 'token_version'"
                    ).fetchone()

            def versions():
                with psycopg.connect(target_pg) as c:
                    return (
                        c.execute("SELECT version_num FROM alembic_version").fetchone()[0],
                        c.execute("SELECT count(*) FROM users WHERE username = 'captest_old'").fetchone()[0],
                    )

            assert column() is None
            self._alembic(target_sa, "upgrade", REVISION)
            assert column() == ("NO", "0")
            assert versions() == (REVISION, 1)
            with psycopg.connect(target_pg) as c:
                assert c.execute("SELECT token_version FROM users WHERE username = 'captest_old'").fetchone()[0] == 0

            self._alembic(target_sa, "downgrade", "-1")
            assert column() is None
            assert versions() == (PREV_REVISION, 1)  # 기존 행은 그대로

            self._alembic(target_sa, "upgrade", REVISION)
            assert column() == ("NO", "0")
            assert versions() == (REVISION, 1)
        finally:
            with psycopg.connect(admin, autocommit=True) as c:
                c.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
