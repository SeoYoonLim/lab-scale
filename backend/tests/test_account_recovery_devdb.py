"""계정 복구를 실제 dev DB로 검증한다(가짜 발송기 = conftest의 mail_outbox, 외부 메일 서버에 연결하지 않음).

- 가입 → 이메일 등록 → 인증 → 아이디 찾기 → 비밀번호 재설정 → 새 비밀번호 로그인 전체 흐름.
- 보안: 사용자 열거 방지(같은 상태 코드·본문), 인증 안 된 이메일엔 메일 없음, 1회용, 만료, 다른 목적 토큰 거부, 해시만 저장,
  새 토큰 발급 시 이전 토큰 무효, 이메일 변경 시 인증 초기화, 동시 사용 시 한 번만 성공, 재설정 후 기존 JWT 폐기, 잠금 해제,
  로그·콘솔에 토큰 원문이 남지 않음.
- 테스트 계정은 captest_ + 임의값이고 signup 픽스처가 끝에 지운다(account_token은 users FK CASCADE).
- 마이그레이션 왕복은 MIGRATION_TEST_DATABASE_URL이 가리키는 별도 서버의 임시 DB에서만 한다(dev DB에서는 하지 않는다).
"""

import logging
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
from app import account_recovery_db as recovery_db
from app import rate_limit
from app.auth import hash_password
from app.db.session import SessionLocal
from app.main import app
from test_auth_revoke_devdb import BACKEND, _migration_server_url

client = TestClient(app, raise_server_exceptions=False)

pytestmark = pytest.mark.integration

TOKEN_RE = re.compile(r"#token=([A-Za-z0-9_-]+)")
REVISION, PREV_REVISION = "b6e1d4a9c2f7", "f4b81d29c6e5"


def dummy_password() -> str:
    return "dummy-" + secrets.token_urlsafe(12)


def email_for(tag: str = "") -> str:
    return f"captest-{tag}{secrets.token_hex(5)}@example.com"


@pytest.fixture
def captest(signup):
    def _make(email: str | None = None):
        if email is None:
            return signup(f"captest_{secrets.token_hex(6)}")
        password = dummy_password()
        username = f"captest_{secrets.token_hex(6)}"
        r = client.post("/api/auth/signup", json={"username": username, "password": password, "email": email})
        assert r.status_code == 201, r.text
        body = r.json()
        _EXTRA_USERS.append(body["user"]["id"])  # 이메일을 넣은 가입은 signup 픽스처를 거치지 않으므로 아래 픽스처가 지운다
        return {**body["user"], "password": password, "headers": {"Authorization": f"Bearer {body['access_token']}"}}

    return _make


_EXTRA_USERS = []


@pytest.fixture(autouse=True)
def _cleanup_extra_users(dev_db):
    yield
    dev_db.rollback()
    while _EXTRA_USERS:
        uid = _EXTRA_USERS.pop()
        dev_db.execute(text("DELETE FROM virtual_account WHERE device_id = :k"), {"k": f"user:{uid}"})
        dev_db.execute(text("DELETE FROM users WHERE id = :i"), {"i": uid})
    dev_db.commit()


def last_token(outbox, to: str) -> str:
    mails = [m for m in outbox if m.to == to]
    assert mails, f"{R.mask_email(to)}에 보낸 메일이 없다"
    return TOKEN_RE.search(mails[-1].body).group(1)


def register_and_verify(user, outbox) -> str:
    email = email_for()
    r = client.put("/api/auth/email", headers=user["headers"], json={"email": email})
    assert r.status_code == 200 and r.json()["verification_sent"] is True
    assert client.post("/api/auth/verify-email", json={"token": last_token(outbox, email)}).status_code == 200
    return email


def tokens_of(dev_db, user_id, purpose=None):
    q = "SELECT token_hash, purpose, expires_at, used_at FROM account_token WHERE user_id = :u"
    rows = dev_db.execute(text(q + (" AND purpose = :p" if purpose else "") + " ORDER BY id"), {"u": user_id, "p": purpose}).all()
    dev_db.rollback()
    return rows


class TestFullFlow:
    def test_signup_register_verify_find_reset_login(self, captest, dev_db, mail_outbox, caplog, capsys):
        caplog.set_level(logging.DEBUG)
        user = captest()
        old_password = user["password"]
        assert client.get("/api/auth/me", headers=user["headers"]).json()["email"] is None

        # 이메일 등록 → 인증 메일(토큰은 fragment에만)
        email = email_for("Flow").upper()
        r = client.put("/api/auth/email", headers=user["headers"], json={"email": f"  {email} "})
        assert r.status_code == 200
        assert r.json() == {"email": email.lower(), "email_verified": False, "verification_sent": True,
                            "message": "인증 메일을 보냈습니다. 메일의 링크를 열면 인증이 끝납니다."}
        email = email.lower()
        verify_raw = last_token(mail_outbox.outbox, email)
        assert f"/verify-email#token={verify_raw}" in mail_outbox.outbox[-1].body

        # 해시만 저장(원문 없음)
        rows = tokens_of(dev_db, user["id"], "verify_email")
        assert len(rows) == 1 and rows[0].token_hash == R.hash_token(verify_raw) and rows[0].token_hash != verify_raw
        assert dev_db.execute(text("SELECT count(*) FROM account_token WHERE token_hash = :t"), {"t": verify_raw}).scalar() == 0
        dev_db.rollback()

        # 인증
        r = client.post("/api/auth/verify-email", json={"token": verify_raw})
        assert r.status_code == 200 and r.json() == {"ok": True, "message": "이메일 인증이 끝났습니다."}
        me = client.get("/api/auth/me", headers=user["headers"]).json()
        assert me == {"id": user["id"], "username": user["username"], "email": email, "email_verified": True}

        # 아이디 찾기(응답에는 아이디가 없고 메일에만)
        r = client.post("/api/auth/find-username", json={"email": email.upper()})
        assert r.status_code == 200 and user["username"] not in r.text
        assert mail_outbox.outbox[-1].to == email and f"아이디: {user['username']}" in mail_outbox.outbox[-1].body

        # 비밀번호 재설정
        assert client.post("/api/auth/forgot-password", json={"email": email}).status_code == 200
        reset_raw = last_token(mail_outbox.outbox, email)
        assert "/reset-password#token=" in mail_outbox.outbox[-1].body
        new_password = dummy_password()
        r = client.post("/api/auth/reset-password", json={"token": reset_raw, "new_password": new_password})
        assert r.status_code == 200 and new_password not in r.text and "access_token" not in r.text

        # 기존 JWT는 폐기, 새 비밀번호만 로그인
        assert client.get("/api/auth/me", headers=user["headers"]).status_code == 401
        assert client.post("/api/auth/login", json={"username": user["username"], "password": old_password}).status_code == 401
        r = client.post("/api/auth/login", json={"username": user["username"], "password": new_password})
        assert r.status_code == 200 and set(r.json()) == {"user", "access_token", "token_type"}

        # 로그·콘솔 어디에도 토큰 원문·비밀번호가 없다
        out = capsys.readouterr()
        for secret in (verify_raw, reset_raw, new_password, old_password):
            assert secret not in caplog.text and secret not in out.out and secret not in out.err

    def test_signup_with_email_sends_verification(self, captest, dev_db, mail_outbox):
        email = email_for("signup")
        user = captest(email=email.upper())
        assert client.get("/api/auth/me", headers=user["headers"]).json()["email"] == email and len(mail_outbox.outbox) == 1
        assert client.post("/api/auth/verify-email", json={"token": last_token(mail_outbox.outbox, email)}).status_code == 200
        assert client.get("/api/auth/me", headers=user["headers"]).json()["email_verified"] is True


class TestEnumerationAndUnverified:
    @pytest.mark.parametrize("path", ["/api/auth/find-username", "/api/auth/forgot-password"])
    def test_existing_missing_and_unverified_look_identical(self, captest, mail_outbox, path):
        verified_user, unverified_user = captest(), captest()
        verified = register_and_verify(verified_user, mail_outbox.outbox)
        unverified = email_for("unv")
        assert client.put("/api/auth/email", headers=unverified_user["headers"], json={"email": unverified}).status_code == 200
        before = len(mail_outbox.outbox)
        rs = [client.post(path, json={"email": e}) for e in (verified, email_for("none"), unverified)]
        assert [r.status_code for r in rs] == [200, 200, 200] and len({r.text for r in rs}) == 1
        sent = mail_outbox.outbox[before:]
        assert [m.to for m in sent] == [verified]  # 인증 안 된 이메일과 없는 이메일에는 아무것도 가지 않는다

    def test_unverified_email_gets_no_reset_token(self, captest, dev_db, mail_outbox):
        user = captest()
        email = email_for("unv")
        client.put("/api/auth/email", headers=user["headers"], json={"email": email})
        mail_outbox.outbox.clear()
        assert client.post("/api/auth/forgot-password", json={"email": email}).status_code == 200
        assert mail_outbox.outbox == [] and tokens_of(dev_db, user["id"], "reset_password") == []


class TestTokenRules:
    def test_verify_token_is_single_use(self, captest, mail_outbox):
        user = captest()
        email = email_for()
        client.put("/api/auth/email", headers=user["headers"], json={"email": email})
        raw = last_token(mail_outbox.outbox, email)
        assert client.post("/api/auth/verify-email", json={"token": raw}).status_code == 200
        r = client.post("/api/auth/verify-email", json={"token": raw})
        assert r.status_code == 400 and r.json() == {"detail": R.INVALID_LINK}

    def test_reset_token_is_single_use(self, captest, mail_outbox):
        user = captest()
        email = register_and_verify(user, mail_outbox.outbox)
        client.post("/api/auth/forgot-password", json={"email": email})
        raw = last_token(mail_outbox.outbox, email)
        assert client.post("/api/auth/reset-password", json={"token": raw, "new_password": dummy_password()}).status_code == 200
        r = client.post("/api/auth/reset-password", json={"token": raw, "new_password": dummy_password()})
        assert r.status_code == 400 and r.json() == {"detail": R.INVALID_LINK}

    def test_expired_tokens_are_rejected(self, captest, mail_outbox, monkeypatch):
        user = captest()
        email = email_for()
        client.put("/api/auth/email", headers=user["headers"], json={"email": email})
        verify_raw = last_token(mail_outbox.outbox, email)
        real_now = recovery_db.now
        monkeypatch.setattr(recovery_db, "now", lambda: real_now() + timedelta(hours=24, seconds=1))
        assert client.post("/api/auth/verify-email", json={"token": verify_raw}).status_code == 400
        monkeypatch.setattr(recovery_db, "now", real_now)
        assert client.post("/api/auth/resend-verification", headers=user["headers"]).status_code == 200
        assert client.post("/api/auth/verify-email", json={"token": last_token(mail_outbox.outbox, email)}).status_code == 200

        client.post("/api/auth/forgot-password", json={"email": email})
        reset_raw = last_token(mail_outbox.outbox, email)
        monkeypatch.setattr(recovery_db, "now", lambda: real_now() + timedelta(minutes=30, seconds=1))
        assert client.post("/api/auth/reset-password", json={"token": reset_raw, "new_password": dummy_password()}).status_code == 400
        monkeypatch.setattr(recovery_db, "now", lambda: real_now() + timedelta(minutes=29))
        assert client.post("/api/auth/reset-password", json={"token": reset_raw, "new_password": dummy_password()}).status_code == 200

    def test_tokens_cannot_cross_purposes(self, captest, mail_outbox):
        user = captest()
        email = email_for()
        client.put("/api/auth/email", headers=user["headers"], json={"email": email})
        verify_raw = last_token(mail_outbox.outbox, email)
        assert client.post("/api/auth/reset-password", json={"token": verify_raw, "new_password": dummy_password()}).status_code == 400
        assert client.post("/api/auth/verify-email", json={"token": verify_raw}).status_code == 200  # 위 거부가 토큰을 쓰지 않았다
        client.post("/api/auth/forgot-password", json={"email": email})
        reset_raw = last_token(mail_outbox.outbox, email)
        assert client.post("/api/auth/verify-email", json={"token": reset_raw}).status_code == 400
        assert client.post("/api/auth/reset-password", json={"token": reset_raw, "new_password": dummy_password()}).status_code == 200

    def test_new_token_invalidates_the_previous_one(self, captest, mail_outbox, dev_db):
        user = captest()
        email = email_for()
        client.put("/api/auth/email", headers=user["headers"], json={"email": email})
        first = last_token(mail_outbox.outbox, email)
        assert client.post("/api/auth/resend-verification", headers=user["headers"]).status_code == 200
        second = last_token(mail_outbox.outbox, email)
        assert first != second
        assert client.post("/api/auth/verify-email", json={"token": first}).status_code == 400
        assert client.post("/api/auth/verify-email", json={"token": second}).status_code == 200

        client.post("/api/auth/forgot-password", json={"email": email})
        r1 = last_token(mail_outbox.outbox, email)
        client.post("/api/auth/forgot-password", json={"email": email})
        r2 = last_token(mail_outbox.outbox, email)
        assert client.post("/api/auth/reset-password", json={"token": r1, "new_password": dummy_password()}).status_code == 400
        assert client.post("/api/auth/reset-password", json={"token": r2, "new_password": dummy_password()}).status_code == 200
        assert all(row.used_at is not None for row in tokens_of(dev_db, user["id"]))

    def test_reset_invalidates_all_other_reset_tokens(self, captest, mail_outbox, dev_db):
        user = captest()
        email = register_and_verify(user, mail_outbox.outbox)
        client.post("/api/auth/forgot-password", json={"email": email})
        raw = last_token(mail_outbox.outbox, email)
        # 다른 경로로 생긴 미사용 재설정 토큰(예: 동시 요청)도 성공한 재설정이 함께 무효로 한다
        db = SessionLocal()
        try:
            db.add(recovery_db.AccountToken(user_id=user["id"], purpose="reset_password", token_hash=R.hash_token("other-dummy"),
                                            expires_at=recovery_db.now() + timedelta(minutes=30)))
            db.commit()
        finally:
            db.close()
        assert client.post("/api/auth/reset-password", json={"token": raw, "new_password": dummy_password()}).status_code == 200
        assert client.post("/api/auth/reset-password", json={"token": "other-dummy", "new_password": dummy_password()}).status_code == 400

    def test_changing_email_resets_verification_and_kills_old_link(self, captest, mail_outbox):
        user = captest()
        first = email_for("a")
        client.put("/api/auth/email", headers=user["headers"], json={"email": first})
        old_link = last_token(mail_outbox.outbox, first)
        second = email_for("b")
        r = client.put("/api/auth/email", headers=user["headers"], json={"email": second})
        assert r.json()["email"] == second and r.json()["email_verified"] is False
        assert client.post("/api/auth/verify-email", json={"token": old_link}).status_code == 400  # 옛 주소의 링크로 새 주소 인증 불가
        assert client.post("/api/auth/verify-email", json={"token": last_token(mail_outbox.outbox, second)}).status_code == 200
        third = email_for("c")
        client.put("/api/auth/email", headers=user["headers"], json={"email": third})
        me = client.get("/api/auth/me", headers=user["headers"]).json()
        assert me["email"] == third and me["email_verified"] is False  # 인증된 주소를 바꾸면 인증이 풀린다

    def test_garbage_tokens(self):
        for t in ("x", "a" * 43, R.new_token()[0]):
            assert client.post("/api/auth/verify-email", json={"token": t}).status_code == 400
            assert client.post("/api/auth/reset-password", json={"token": t, "new_password": dummy_password()}).status_code == 400


class TestConcurrency:
    def test_same_reset_token_twice_at_once_succeeds_once(self, captest, mail_outbox, dev_db):
        user = captest()
        email = register_and_verify(user, mail_outbox.outbox)
        client.post("/api/auth/forgot-password", json={"email": email})
        raw = last_token(mail_outbox.outbox, email)
        before = dev_db.execute(text("SELECT token_version FROM users WHERE id = :i"), {"i": user["id"]}).scalar()
        dev_db.rollback()
        hashes = [hash_password(dummy_password()) for _ in range(2)]
        barrier = threading.Barrier(2)
        results = [None, None]

        def worker(i):
            db = SessionLocal()
            try:
                barrier.wait()
                results[i] = recovery_db.reset_password(db, raw, hashes[i], recovery_db.now())
            finally:
                db.close()

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(30)
        assert sum(r is not None for r in results) == 1, results
        after = dev_db.execute(text("SELECT token_version FROM users WHERE id = :i"), {"i": user["id"]}).scalar()
        dev_db.rollback()
        assert after == before + 1


class TestConflictsAndLocks:
    def test_duplicate_email_is_409_case_insensitive(self, captest, mail_outbox):
        a, b = captest(), captest()
        email = email_for("dup")
        assert client.put("/api/auth/email", headers=a["headers"], json={"email": email}).status_code == 200
        r = client.put("/api/auth/email", headers=b["headers"], json={"email": email.upper()})
        assert r.status_code == 409 and r.json() == {"detail": "이미 사용 중인 이메일입니다."}
        r = client.post("/api/auth/signup", json={"username": f"captest_{secrets.token_hex(6)}", "password": dummy_password(), "email": email})
        assert r.status_code == 409 and r.json() == {"detail": "이미 사용 중인 이메일입니다."}
        # 409로 거부된 가입은 계정을 만들지 않았다
        assert client.get("/api/auth/me", headers=b["headers"]).json()["email"] is None

    def test_same_email_again_is_a_no_op(self, captest, mail_outbox):
        user = captest()
        email = register_and_verify(user, mail_outbox.outbox)
        n = len(mail_outbox.outbox)
        r = client.put("/api/auth/email", headers=user["headers"], json={"email": email.upper()})
        assert r.status_code == 200 and r.json()["verification_sent"] is False and r.json()["email_verified"] is True
        assert len(mail_outbox.outbox) == n

    def test_reset_password_policy_is_422_and_token_survives(self, captest, mail_outbox):
        user = captest()
        email = register_and_verify(user, mail_outbox.outbox)
        client.post("/api/auth/forgot-password", json={"email": email})
        raw = last_token(mail_outbox.outbox, email)
        assert client.post("/api/auth/reset-password", json={"token": raw, "new_password": "short"}).status_code == 422
        assert client.post("/api/auth/reset-password", json={"token": raw, "new_password": dummy_password()}).status_code == 200

    def test_reset_clears_login_lock(self, captest, mail_outbox, monkeypatch):
        user = captest()
        email = register_and_verify(user, mail_outbox.outbox)
        monkeypatch.setenv("RATE_LIMIT_ENABLED", "1")
        rate_limit.reset_all()
        for _ in range(5):
            assert client.post("/api/auth/login", json={"username": user["username"], "password": "wrong-dummy-pw"}).status_code == 401
        assert client.post("/api/auth/login", json={"username": user["username"], "password": user["password"]}).status_code == 429
        client.post("/api/auth/forgot-password", json={"email": email})
        new_password = dummy_password()
        assert client.post("/api/auth/reset-password", json={"token": last_token(mail_outbox.outbox, email), "new_password": new_password}).status_code == 200
        assert client.post("/api/auth/login", json={"username": user["username"], "password": new_password}).status_code == 200

    def test_existing_signup_and_login_without_email_still_work(self, captest):
        user = captest()
        r = client.post("/api/auth/login", json={"username": user["username"], "password": user["password"]})
        assert r.status_code == 200 and r.json()["user"] == {"id": user["id"], "username": user["username"]}


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
                cols = [r[0] for r in c.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'users' ORDER BY 1")]
                tables = [r[0] for r in c.execute("SELECT table_name FROM information_schema.tables WHERE table_name = 'account_token'")]
                idx = sorted(r[0] for r in c.execute("SELECT indexname FROM pg_indexes WHERE tablename IN ('users', 'account_token')"))
                ver = c.execute("SELECT version_num FROM alembic_version").fetchone()[0]
            return cols, tables, idx, ver

        with psycopg.connect(admin, autocommit=True) as c:
            c.execute(f'CREATE DATABASE "{name}"')
        try:
            self._alembic(target_sa, "upgrade", PREV_REVISION)
            cols, tables, _, _ = state()
            assert "email" not in cols and tables == []
            self._alembic(target_sa, "upgrade", REVISION)
            cols, tables, idx, ver = state()
            assert ver == REVISION and {"email", "email_verified_at"} <= set(cols) and tables == ["account_token"]
            assert {"uq_users_email_lower", "uq_account_token_hash", "idx_account_token_user_purpose"} <= set(idx)
            with psycopg.connect(target_pg, autocommit=True) as c:
                c.execute("INSERT INTO users (username, password_hash, email) VALUES ('m1', 'x', 'a@b.com')")
                with pytest.raises(psycopg.errors.UniqueViolation):  # 대소문자 무시 유일(정규화 CHECK보다 인덱스가 먼저 보이게 소문자로)
                    c.execute("INSERT INTO users (username, password_hash, email) VALUES ('m2', 'x', 'a@b.com')")
                with pytest.raises(psycopg.errors.CheckViolation):  # 정규화되지 않은 값은 저장 불가
                    c.execute("INSERT INTO users (username, password_hash, email) VALUES ('m3', 'x', ' A@B.com')")
                uid = c.execute("SELECT id FROM users WHERE username = 'm1'").fetchone()[0]
                c.execute("INSERT INTO account_token (user_id, purpose, token_hash, expires_at) VALUES (%s, 'verify_email', 'h', now())", (uid,))
                with pytest.raises(psycopg.errors.UniqueViolation):
                    c.execute("INSERT INTO account_token (user_id, purpose, token_hash, expires_at) VALUES (%s, 'reset_password', 'h', now())", (uid,))
                with pytest.raises(psycopg.errors.CheckViolation):
                    c.execute("INSERT INTO account_token (user_id, purpose, token_hash, expires_at) VALUES (%s, 'login', 'h2', now())", (uid,))
                c.execute("DELETE FROM users WHERE id = %s", (uid,))  # ON DELETE CASCADE
                assert c.execute("SELECT count(*) FROM account_token").fetchone()[0] == 0
            self._alembic(target_sa, "downgrade", "-1")
            cols, tables, idx, ver = state()
            assert ver == PREV_REVISION and "email" not in cols and tables == [] and "uq_users_email_lower" not in idx
            self._alembic(target_sa, "upgrade", REVISION)
            assert state()[3] == REVISION
        finally:
            with psycopg.connect(admin, autocommit=True) as c:
                c.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
