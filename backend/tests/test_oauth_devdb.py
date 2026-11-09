"""소셜 로그인을 실제 dev DB로 검증한다(공급자 HTTP는 가짜, 실제 구글·네이버에 요청하지 않음).

- 전체 흐름(login → callback → exchange → 우리 JWT로 인증 API), 같은 identity 재로그인은 같은 사용자, 네이버 흐름.
- 계정 연결 규칙: 양쪽 검증된 같은 이메일만 연결, 로컬 미인증·공급자 미검증이면 이메일 없이 새 계정.
- 1회용 코드: 해시만 저장, 재사용·만료 거부, 동시 두 번 교환 시 한 번만 성공.
- 비밀번호 없는 계정: 비밀번호 로그인은 일반 401, 비밀번호 변경 400, 이메일 변경 400, 비밀번호 찾기로 설정하면 이후 로그인 가능.
- 정리: 소셜 계정은 provider_user_id가 captest- 로 시작하는 identity로 찾아 사용자째 지운다(identity·토큰은 CASCADE).
  로컬 계정은 signup 픽스처(captest_)가 지운다.
- 마이그레이션 왕복은 MIGRATION_TEST_DATABASE_URL의 별도 서버에서만 한다.
"""

import os
import re
import secrets
import subprocess
import sys
import threading
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app import account_recovery as R
from app import oauth, oauth_db, oauth_providers
from app.db.session import SessionLocal
from app.main import app
from oauth_fakes import FakeProvider, callback, code_from, configure, google_claims, start
from test_auth_revoke_devdb import BACKEND, _migration_server_url

pytestmark = pytest.mark.integration

REVISION, PREV_REVISION = "c7a2e5f81d30", "b6e1d4a9c2f7"
TOKEN_RE = re.compile(r"#token=([A-Za-z0-9_-]+)")


def sub() -> str:
    return f"captest-{secrets.token_hex(6)}"


def mail() -> str:
    return f"captest-o{secrets.token_hex(5)}@example.com"


@pytest.fixture(autouse=True)
def _cleanup(dev_db):
    yield
    dev_db.rollback()
    ids = [r[0] for r in dev_db.execute(text("SELECT DISTINCT user_id FROM oauth_identity WHERE provider_user_id LIKE 'captest-%'"))]
    for uid in ids:
        dev_db.execute(text("DELETE FROM virtual_account WHERE device_id = :k"), {"k": f"user:{uid}"})
        dev_db.execute(text("DELETE FROM users WHERE id = :i AND (username LIKE 'google\\_%' OR username LIKE 'naver\\_%')"), {"i": uid})
    dev_db.commit()


@pytest.fixture
def fake(monkeypatch):
    f = FakeProvider()
    monkeypatch.setattr(oauth_providers, "HTTP", f)
    configure(monkeypatch)
    return f


def social_login(fake, provider="google", sid=None, email="", verified=True, exchange=True):
    """새 브라우저(TestClient)로 로그인 시작 → 가짜 공급자 → 콜백. exchange면 (로그인 응답 JSON, client), 아니면 (1회용 코드, client)."""
    client = TestClient(app, raise_server_exceptions=False)
    sid = sid or sub()
    email = mail() if email == "" else email
    _, _, state, nonce = start(client, provider)
    if provider == "google":
        fake.google_claims = google_claims(sub=sid, email=email, email_verified=verified, nonce=nonce)
    else:
        info = {"id": sid, "name": "테스트"}
        if email:
            info["email"] = email
        fake.naver_profile = {"resultcode": "00", "message": "success", "response": info}
    r = callback(client, provider, state)
    assert r.status_code == 302 and "/oauth/callback#code=" in r.headers["location"], r.headers.get("location")
    code = code_from(r.headers["location"])
    if not exchange:
        return code, client
    r = client.post("/api/auth/oauth/exchange", json={"code": code})
    assert r.status_code == 200, r.text
    return r.json(), client


def bearer(body):
    return {"Authorization": f"Bearer {body['access_token']}"}


def me(body):
    return TestClient(app).get("/api/auth/me", headers=bearer(body)).json()


def local_verified(signup, outbox, email):
    """로컬 아이디/비밀번호 계정 + 이메일 등록·인증."""
    user = signup(f"captest_{secrets.token_hex(6)}")
    c = TestClient(app)
    assert c.put("/api/auth/email", headers=user["headers"], json={"email": email, "current_password": user["password"]}).status_code == 200
    raw = TOKEN_RE.search(outbox[-1].body).group(1)
    assert c.post("/api/auth/verify-email", json={"token": raw}).status_code == 200
    return user


class TestFlow:
    def test_google_new_account_full_flow(self, fake, dev_db):
        email = mail()
        body, client = social_login(fake, "google", email=email.upper())
        assert set(body) == {"user", "access_token", "token_type"} and body["token_type"] == "bearer"
        uname = body["user"]["username"]
        assert re.fullmatch(r"google_[0-9a-f]{8}", uname)
        info = me(body)
        assert info == {"id": body["user"]["id"], "username": uname, "email": email, "email_verified": True}
        # 우리 JWT로 인증 API가 된다
        assert TestClient(app).get("/api/portfolio/exposure", headers=bearer(body)).status_code == 200
        row = dev_db.execute(text("SELECT password_hash IS NULL, email_verified_at IS NOT NULL FROM users WHERE id = :i"),
                             {"i": body["user"]["id"]}).one()
        ident = dev_db.execute(text("SELECT provider, email FROM oauth_identity WHERE user_id = :i"), {"i": body["user"]["id"]}).all()
        dev_db.rollback()
        assert tuple(row) == (True, True) and [tuple(x) for x in ident] == [("google", email)]

    def test_same_identity_logs_into_the_same_user(self, fake, dev_db):
        sid = sub()
        first, _ = social_login(fake, "google", sid=sid)
        before = dev_db.execute(text("SELECT count(*) FROM users")).scalar()
        dev_db.rollback()
        again, _ = social_login(fake, "google", sid=sid, email=mail())  # 공급자 쪽 이메일이 바뀌어도 identity로 찾는다
        assert again["user"] == first["user"]
        assert dev_db.execute(text("SELECT count(*) FROM users")).scalar() == before
        dev_db.rollback()

    def test_naver_flow_and_no_email_consent(self, fake):
        body, _ = social_login(fake, "naver", email=mail())
        assert body["user"]["username"].startswith("naver_") and me(body)["email_verified"] is True
        body2, _ = social_login(fake, "naver", email=None)
        assert me(body2)["email"] is None and me(body2)["email_verified"] is False

    def test_usernames_are_unique(self, fake):
        names = {social_login(fake, "google")[0]["user"]["username"] for _ in range(5)}
        assert len(names) == 5


class TestLinking:
    def test_links_only_when_both_sides_verified(self, fake, signup, mail_outbox):
        email = mail()
        local = local_verified(signup, mail_outbox.outbox, email)
        body, _ = social_login(fake, "google", email=email, verified=True)
        assert body["user"] == {"id": local["id"], "username": local["username"]}  # 같은 사람으로 연결
        # 연결 뒤에도 기존 아이디/비밀번호 로그인은 그대로
        r = TestClient(app).post("/api/auth/login", json={"username": local["username"], "password": local["password"]})
        assert r.status_code == 200

    def test_no_link_when_local_email_unverified(self, fake, signup):
        email = mail()
        local = signup(f"captest_{secrets.token_hex(6)}")
        TestClient(app).put("/api/auth/email", headers=local["headers"], json={"email": email, "current_password": local["password"]})
        body, _ = social_login(fake, "google", email=email, verified=True)
        assert body["user"]["id"] != local["id"]
        info = me(body)
        assert info["email"] is None and info["email_verified"] is False  # 충돌 시 이메일 없이 새 계정

    def test_no_link_when_provider_email_unverified(self, fake, signup, mail_outbox):
        email = mail()
        local = local_verified(signup, mail_outbox.outbox, email)
        body, _ = social_login(fake, "google", email=email, verified=False)
        assert body["user"]["id"] != local["id"] and me(body)["email"] is None

    def test_unverified_provider_email_is_not_stored(self, fake):
        body, _ = social_login(fake, "google", email=mail(), verified=False)
        assert me(body)["email"] is None


class TestOneTimeCode:
    def test_code_is_hashed_single_use_and_expires(self, fake, dev_db, monkeypatch):
        code, client = social_login(fake, "google", exchange=False)
        assert dev_db.execute(text("SELECT count(*) FROM account_token WHERE token_hash = :c"), {"c": code}).scalar() == 0
        assert dev_db.execute(text("SELECT count(*) FROM account_token WHERE token_hash = :h AND purpose = 'oauth_login'"),
                              {"h": R.hash_token(code)}).scalar() == 1
        dev_db.rollback()
        assert client.post("/api/auth/oauth/exchange", json={"code": code}).status_code == 200
        r = client.post("/api/auth/oauth/exchange", json={"code": code})
        assert r.status_code == 400 and r.json() == {"detail": "로그인 코드가 올바르지 않거나 만료됐습니다. 다시 로그인해주세요."}

        code2, client2 = social_login(fake, "google", exchange=False)
        real_now = oauth.now
        monkeypatch.setattr(oauth, "now", lambda: real_now() + timedelta(seconds=61))
        assert client2.post("/api/auth/oauth/exchange", json={"code": code2}).status_code == 400

    def test_other_token_purposes_are_not_login_codes(self, fake, signup, mail_outbox):
        user = signup(f"captest_{secrets.token_hex(6)}")
        TestClient(app).put("/api/auth/email", headers=user["headers"], json={"email": mail(), "current_password": user["password"]})
        verify_raw = TOKEN_RE.search(mail_outbox.outbox[-1].body).group(1)
        assert TestClient(app).post("/api/auth/oauth/exchange", json={"code": verify_raw}).status_code == 400

    def test_concurrent_exchange_succeeds_once(self, fake):
        code, _ = social_login(fake, "google", exchange=False)
        barrier = threading.Barrier(2)
        results = [None, None]

        def worker(i):
            db = SessionLocal()
            try:
                barrier.wait()
                results[i] = oauth_db.exchange_login_code(db, code, oauth.now())
            finally:
                db.close()

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(30)
        assert sum(r is not None for r in results) == 1


class TestPasswordlessAccount:
    def test_password_login_fails_like_unknown_user(self, fake):
        body, _ = social_login(fake, "google")
        c = TestClient(app)
        r = c.post("/api/auth/login", json={"username": body["user"]["username"], "password": "any-dummy-password"})
        unknown = c.post("/api/auth/login", json={"username": f"captest_{secrets.token_hex(6)}", "password": "any-dummy-password"})
        assert r.status_code == unknown.status_code == 401 and r.json() == unknown.json()

    def test_change_password_and_email_change_are_400(self, fake):
        body, _ = social_login(fake, "google")
        c = TestClient(app)
        r = c.post("/api/auth/change-password", headers=bearer(body), json={"current_password": "x-dummy-pw", "new_password": "y-dummy-pw-long"})
        assert r.status_code == 400 and "소셜 계정은 비밀번호가 없습니다" in r.json()["detail"]
        r = c.put("/api/auth/email", headers=bearer(body), json={"email": mail(), "current_password": "x-dummy-pw"})
        assert r.status_code == 400 and "소셜 로그인" in r.json()["detail"]
        assert c.get("/api/auth/me", headers=bearer(body)).status_code == 200  # 토큰은 그대로 유효

    def test_forgot_password_sets_a_password(self, fake, mail_outbox):
        email = mail()
        body, _ = social_login(fake, "google", email=email)
        c = TestClient(app)
        assert c.post("/api/auth/forgot-password", json={"email": email}).status_code == 200
        raw = TOKEN_RE.search(mail_outbox.outbox[-1].body).group(1)
        new_pw = "dummy-" + secrets.token_urlsafe(12)
        assert c.post("/api/auth/reset-password", json={"token": raw, "new_password": new_pw}).status_code == 200
        r = c.post("/api/auth/login", json={"username": body["user"]["username"], "password": new_pw})
        assert r.status_code == 200 and r.json()["user"]["id"] == body["user"]["id"]
        # 이제 비밀번호가 있으니 비밀번호 변경도 된다(재설정이 이전 JWT를 폐기했으므로 새 토큰으로)
        r2 = c.post("/api/auth/change-password", headers=bearer(r.json()),
                    json={"current_password": new_pw, "new_password": "dummy-" + secrets.token_urlsafe(12)})
        assert r2.status_code == 200


class TestMigrationRoundTrip:
    def _alembic(self, db_url: str, *args: str) -> None:
        env = {**os.environ, "DATABASE_URL": db_url}
        r = subprocess.run([sys.executable, "-m", "alembic", *args], cwd=BACKEND, env=env, capture_output=True, text=True, timeout=180)
        assert r.returncode == 0, r.stderr[-2000:]

    def test_upgrade_downgrade_upgrade(self):
        import psycopg

        server = _migration_server_url()
        name = f"captest_migr_{secrets.token_hex(4)}"
        admin = server.set(drivername="postgresql").render_as_string(hide_password=False)
        target = server.set(database=name)
        target_sa = target.render_as_string(hide_password=False)
        target_pg = target.set(drivername="postgresql").render_as_string(hide_password=False)

        def state():
            with psycopg.connect(target_pg) as c:
                nullable = c.execute("SELECT is_nullable FROM information_schema.columns WHERE table_name='users' AND column_name='password_hash'").fetchone()[0]
                tables = [r[0] for r in c.execute("SELECT table_name FROM information_schema.tables WHERE table_name='oauth_identity'")]
                ver = c.execute("SELECT version_num FROM alembic_version").fetchone()[0]
            return nullable, tables, ver

        with psycopg.connect(admin, autocommit=True) as c:
            c.execute(f'CREATE DATABASE "{name}"')
        try:
            self._alembic(target_sa, "upgrade", PREV_REVISION)
            assert state() == ("NO", [], PREV_REVISION)
            self._alembic(target_sa, "upgrade", REVISION)
            assert state() == ("YES", ["oauth_identity"], REVISION)
            with psycopg.connect(target_pg, autocommit=True) as c:
                uid = c.execute("INSERT INTO users (username, password_hash) VALUES ('g1', NULL) RETURNING id").fetchone()[0]
                c.execute("INSERT INTO oauth_identity (user_id, provider, provider_user_id) VALUES (%s, 'google', 's1')", (uid,))
                with pytest.raises(psycopg.errors.UniqueViolation):
                    c.execute("INSERT INTO oauth_identity (user_id, provider, provider_user_id) VALUES (%s, 'google', 's1')", (uid,))
                with pytest.raises(psycopg.errors.CheckViolation):
                    c.execute("INSERT INTO oauth_identity (user_id, provider, provider_user_id) VALUES (%s, 'kakao', 's2')", (uid,))
                c.execute("INSERT INTO account_token (user_id, purpose, token_hash, expires_at) VALUES (%s, 'oauth_login', 'h1', now())", (uid,))
            self._alembic(target_sa, "downgrade", "-1")
            assert state() == ("NO", [], PREV_REVISION)
            with psycopg.connect(target_pg) as c:
                assert c.execute("SELECT password_hash FROM users WHERE username='g1'").fetchone()[0] == "!no-password"
                assert c.execute("SELECT count(*) FROM account_token").fetchone()[0] == 0
            self._alembic(target_sa, "upgrade", REVISION)
            assert state()[2] == REVISION
        finally:
            with psycopg.connect(admin, autocommit=True) as c:
                c.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
