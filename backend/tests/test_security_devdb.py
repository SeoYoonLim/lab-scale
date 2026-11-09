"""보안 회귀 테스트(실제 dev DB): IDOR(남의 리포트·진단은 404), 회원 탈퇴(비밀번호 계정·소셜 계정 두 방식)의 삭제 범위와
다른 사용자 비영향, 탈퇴 뒤 토큰 401, argon2 재해시, 재설정 비밀번호 문맥 검사(토큰 유지), 로그에 비밀값 없음, 마이그레이션 왕복.

테스트 계정은 captest_ + 임의값이고 끝에 지운다(signup 픽스처 + 직접 만든 계정은 아래 정리 픽스처). 외부 메일은 conftest의 가짜 발송기.
"""

import logging
import os
import re
import secrets
import subprocess
import sys
from datetime import datetime, timedelta, timezone

import pytest
from argon2 import PasswordHasher
from fastapi.testclient import TestClient
from sqlalchemy import text

from app import account_recovery as R
from app.auth import create_access_token
from app.db.session import SessionLocal
from app.main import app
from app.models import (AccountToken, Holding, OAuthIdentity, PortfolioDiagnosis, ResearchReport, ToolCallLog, Trade, User,
                        VirtualAccount, Watchlist)
from test_auth_revoke_devdb import BACKEND, _migration_server_url

pytestmark = pytest.mark.integration
client = TestClient(app, raise_server_exceptions=False)
TOKEN_RE = re.compile(r"#token=([A-Za-z0-9_-]+)")
REVISION, PREV_REVISION = "d4f8a1c6b2e9", "c7a2e5f81d30"
_MADE: list[int] = []


def dummy_pw() -> str:
    return "dummy-" + secrets.token_urlsafe(12)


@pytest.fixture(autouse=True)
def _cleanup(dev_db):
    yield
    dev_db.rollback()
    while _MADE:
        uid = _MADE.pop()
        dev_db.execute(text("DELETE FROM watchlist WHERE device_id = :k"), {"k": f"user:{uid}"})
        dev_db.execute(text("DELETE FROM virtual_account WHERE device_id = :k"), {"k": f"user:{uid}"})
        dev_db.execute(text("DELETE FROM users WHERE id = :i AND username LIKE 'captest\\_%'"), {"i": uid})
    dev_db.commit()


def make_user(password_hash=None, email=None, verified=False) -> dict:
    db = SessionLocal()
    try:
        u = User(username=f"captest_{secrets.token_hex(6)}", password_hash=password_hash, email=email,
                 email_verified_at=datetime.now(timezone.utc) if verified else None)
        db.add(u)
        db.commit()
        _MADE.append(u.id)
        return {"id": u.id, "username": u.username, "headers": {"Authorization": f"Bearer {create_access_token(u.id)}"}}
    finally:
        db.close()


def seed_everything(dev_db, uid: int) -> int:
    """그 사용자의 모든 종류 데이터를 한 줄씩 넣는다. 만든 리포트 id."""
    owner = f"user:{uid}"
    cid = dev_db.execute(text("SELECT id FROM company ORDER BY id LIMIT 1")).scalar()
    dev_db.add(Watchlist(device_id=owner, company_id=cid))
    dev_db.add(VirtualAccount(device_id=owner, cash_balance=1000))
    dev_db.flush()
    dev_db.add(Holding(device_id=owner, company_id=cid, quantity=1, avg_price=1))
    dev_db.add(Trade(device_id=owner, company_id=cid, side="buy", quantity=1, price=1))
    rep = ResearchReport(user_id=uid, question="captest 질문", summary="s")
    dev_db.add(rep)
    dev_db.flush()
    dev_db.add(ToolCallLog(report_id=rep.id, tool_name="stock_tool"))
    dev_db.add(PortfolioDiagnosis(user_id=uid, source="rule", total_asset=1, holdings_count=1, result={}))
    dev_db.add(AccountToken(user_id=uid, purpose="verify_email", token_hash=secrets.token_hex(32), expires_at=datetime.now(timezone.utc)))
    dev_db.add(OAuthIdentity(user_id=uid, provider="google", provider_user_id=f"captest-{secrets.token_hex(6)}"))
    dev_db.commit()
    return rep.id


def counts(dev_db, uid: int) -> dict:
    owner = f"user:{uid}"
    q = {
        "users": ("SELECT count(*) FROM users WHERE id = :u", uid),
        "watchlist": ("SELECT count(*) FROM watchlist WHERE device_id = :u", owner),
        "virtual_account": ("SELECT count(*) FROM virtual_account WHERE device_id = :u", owner),
        "holding": ("SELECT count(*) FROM holding WHERE device_id = :u", owner),
        "trade": ("SELECT count(*) FROM trade WHERE device_id = :u", owner),
        "research_report": ("SELECT count(*) FROM research_report WHERE user_id = :u", uid),
        "tool_call_log": ("SELECT count(*) FROM tool_call_log t JOIN research_report r ON r.id = t.report_id WHERE r.user_id = :u", uid),
        "portfolio_diagnosis": ("SELECT count(*) FROM portfolio_diagnosis WHERE user_id = :u", uid),
        "account_token": ("SELECT count(*) FROM account_token WHERE user_id = :u", uid),
        "oauth_identity": ("SELECT count(*) FROM oauth_identity WHERE user_id = :u", uid),
    }
    out = {k: dev_db.execute(text(sql), {"u": v}).scalar() for k, (sql, v) in q.items()}
    dev_db.rollback()
    return out


class TestIdor:
    def test_other_users_report_and_diagnosis_are_404(self, dev_db):
        a, b = make_user(), make_user()
        rid = seed_everything(dev_db, a["id"])
        did = dev_db.execute(text("SELECT id FROM portfolio_diagnosis WHERE user_id = :u"), {"u": a["id"]}).scalar()
        dev_db.rollback()
        for method, path in [("GET", f"/api/research/{rid}"), ("DELETE", f"/api/research/{rid}"),
                             ("GET", f"/api/portfolio/diagnosis/{did}"), ("GET", f"/api/portfolio/diagnosis/{did}/compare"),
                             ("DELETE", f"/api/portfolio/diagnosis/{did}")]:
            r = client.request(method, path, headers=b["headers"])
            assert r.status_code == 404, (method, path, r.status_code)
        assert client.get(f"/api/research/{rid}", headers=a["headers"]).status_code == 200
        assert counts(dev_db, a["id"])["research_report"] == 1  # 남의 DELETE는 아무것도 지우지 않았다
        # 목록에도 남의 것은 없다
        assert client.get("/api/research", headers=b["headers"]).json()["total"] == 0


class TestAccountDeletion:
    def test_password_account(self, signup, dev_db, caplog):
        caplog.set_level(logging.INFO)
        a, b = signup(f"captest_{secrets.token_hex(6)}"), signup(f"captest_{secrets.token_hex(6)}")
        seed_everything(dev_db, a["id"])
        seed_everything(dev_db, b["id"])
        before_b = counts(dev_db, b["id"])
        assert all(v >= 1 for v in counts(dev_db, a["id"]).values())

        r = client.request("DELETE", "/api/auth/me", headers=a["headers"], json={})
        assert r.status_code == 422 and r.json()["detail"][0]["loc"] == ["body", "current_password"]
        r = client.request("DELETE", "/api/auth/me", headers=a["headers"], json={"current_password": "wrong-dummy-pw"})
        assert r.status_code == 422 and r.json()["detail"][0]["type"] == "invalid_current_password"
        assert counts(dev_db, a["id"])["users"] == 1

        r = client.request("DELETE", "/api/auth/me", headers=a["headers"], json={"current_password": a["password"]})
        assert r.status_code == 200 and r.json() == {"ok": True, "status": "deleted", "message": "회원 탈퇴가 끝났습니다. 계정과 모든 데이터를 지웠습니다."}
        assert set(counts(dev_db, a["id"]).values()) == {0}
        assert counts(dev_db, b["id"]) == before_b  # 다른 사용자는 그대로
        assert client.get("/api/auth/me", headers=a["headers"]).status_code == 401
        assert client.post("/api/auth/login", json={"username": a["username"], "password": a["password"]}).status_code == 401
        assert client.get("/api/auth/me", headers=b["headers"]).status_code == 200
        sec = [rec.getMessage() for rec in caplog.records if rec.name == "app.security"]
        assert any(m.startswith(f"event=account_delete user={a['id']} result=ok") for m in sec)
        assert a["password"] not in caplog.text and "wrong-dummy-pw" not in caplog.text

    def test_social_account_with_verified_email_confirms_by_mail(self, dev_db, mail_outbox):
        email = f"captest-del{secrets.token_hex(4)}@example.com"
        u = make_user(email=email, verified=True)
        other = make_user()
        seed_everything(dev_db, u["id"])
        seed_everything(dev_db, other["id"])
        before_other = counts(dev_db, other["id"])
        r = client.request("DELETE", "/api/auth/me", headers=u["headers"], json={})
        assert r.status_code == 202 and r.json()["status"] == "confirmation_sent"
        assert counts(dev_db, u["id"])["users"] == 1  # 메일 확인 전에는 지우지 않는다
        mail = mail_outbox.outbox[-1]
        assert mail.to == email and "/delete-account#token=" in mail.body and "?token" not in mail.body
        raw = TOKEN_RE.search(mail.body).group(1)
        assert dev_db.execute(text("SELECT count(*) FROM account_token WHERE token_hash = :t"), {"t": raw}).scalar() == 0
        dev_db.rollback()
        assert client.post("/api/auth/delete-account/confirm", json={"token": "wrong-dummy-token"}).status_code == 400
        r = client.post("/api/auth/delete-account/confirm", json={"token": raw})
        assert r.status_code == 200 and r.json()["status"] == "deleted"
        assert set(counts(dev_db, u["id"]).values()) == {0} and counts(dev_db, other["id"]) == before_other
        assert client.post("/api/auth/delete-account/confirm", json={"token": raw}).status_code == 400
        assert client.get("/api/auth/me", headers=u["headers"]).status_code == 401

    def test_other_token_purposes_cannot_confirm_deletion(self, dev_db, mail_outbox):
        u = make_user(email=f"captest-x{secrets.token_hex(4)}@example.com", verified=True)
        client.post("/api/auth/forgot-password", json={"email": dev_db.execute(text("SELECT email FROM users WHERE id=:i"), {"i": u["id"]}).scalar()})
        dev_db.rollback()
        reset_raw = TOKEN_RE.search(mail_outbox.outbox[-1].body).group(1)
        assert client.post("/api/auth/delete-account/confirm", json={"token": reset_raw}).status_code == 400
        assert counts(dev_db, u["id"])["users"] == 1

    def test_social_account_without_email_needs_recent_login_and_username(self, dev_db):
        u = make_user()
        old = create_access_token(u["id"], now=datetime.now(timezone.utc) - timedelta(minutes=20))
        r = client.request("DELETE", "/api/auth/me", headers={"Authorization": f"Bearer {old}"}, json={"confirm_username": u["username"]})
        assert r.status_code == 400 and "10분" in r.json()["detail"]
        r = client.request("DELETE", "/api/auth/me", headers=u["headers"], json={"confirm_username": "someone_else"})
        assert r.status_code == 400
        r = client.request("DELETE", "/api/auth/me", headers=u["headers"], json={"confirm_username": u["username"].upper()})
        assert r.status_code == 200 and counts(dev_db, u["id"])["users"] == 0


class TestPasswordHardening:
    def test_weak_argon2_hash_is_upgraded_on_login(self, dev_db):
        pw = dummy_pw()
        weak = PasswordHasher(time_cost=1, memory_cost=8192, parallelism=1).hash(pw)
        u = make_user(password_hash=weak)
        assert client.post("/api/auth/login", json={"username": u["username"], "password": pw}).status_code == 200
        stored = dev_db.execute(text("SELECT password_hash FROM users WHERE id = :i"), {"i": u["id"]}).scalar()
        dev_db.rollback()
        assert stored != weak and not PasswordHasher().check_needs_rehash(stored)
        assert client.post("/api/auth/login", json={"username": u["username"], "password": pw}).status_code == 200

    def test_reset_rejects_username_in_new_password_and_keeps_the_token(self, dev_db, mail_outbox):
        email = f"captest-r{secrets.token_hex(4)}@example.com"
        u = make_user(password_hash=PasswordHasher().hash(dummy_pw()), email=email, verified=True)
        client.post("/api/auth/forgot-password", json={"email": email})
        raw = TOKEN_RE.search(mail_outbox.outbox[-1].body).group(1)
        r = client.post("/api/auth/reset-password", json={"token": raw, "new_password": f"x-{u['username']}-y"})
        assert r.status_code == 422 and r.json()["detail"][0]["type"] == "password_context" and u["username"] not in r.text
        assert client.post("/api/auth/reset-password", json={"token": raw, "new_password": dummy_pw()}).status_code == 200

    def test_change_password_rejects_username(self, signup):
        a = signup(f"captest_{secrets.token_hex(6)}")
        r = client.post("/api/auth/change-password", headers=a["headers"],
                        json={"current_password": a["password"], "new_password": f"zz{a['username']}zz"})
        assert r.status_code == 422 and r.json()["detail"][0]["type"] == "password_context"


class TestMigrationRoundTrip:
    def _alembic(self, db_url: str, *args: str) -> None:
        env = {**os.environ, "DATABASE_URL": db_url}
        r = subprocess.run([sys.executable, "-m", "alembic", *args], cwd=BACKEND, env=env, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=180)
        assert r.returncode == 0, r.stderr[-2000:]

    def test_upgrade_downgrade_upgrade(self):
        import psycopg

        server = _migration_server_url()
        name = f"captest_migr_{secrets.token_hex(4)}"
        admin = server.set(drivername="postgresql").render_as_string(hide_password=False)
        target = server.set(database=name)
        target_sa = target.render_as_string(hide_password=False)
        target_pg = target.set(drivername="postgresql").render_as_string(hide_password=False)

        def insert_delete_token(c, uid):
            c.execute("INSERT INTO account_token (user_id, purpose, token_hash, expires_at) VALUES (%s, 'delete_account', %s, now())",
                      (uid, secrets.token_hex(8)))

        with psycopg.connect(admin, autocommit=True) as c:
            c.execute(f'CREATE DATABASE "{name}"')
        try:
            self._alembic(target_sa, "upgrade", PREV_REVISION)
            with psycopg.connect(target_pg, autocommit=True) as c:
                uid = c.execute("INSERT INTO users (username, password_hash) VALUES ('m1', NULL) RETURNING id").fetchone()[0]
                with pytest.raises(psycopg.errors.CheckViolation):
                    insert_delete_token(c, uid)
            self._alembic(target_sa, "upgrade", REVISION)
            with psycopg.connect(target_pg, autocommit=True) as c:
                insert_delete_token(c, uid)
            self._alembic(target_sa, "downgrade", "-1")
            with psycopg.connect(target_pg) as c:
                assert c.execute("SELECT count(*) FROM account_token").fetchone()[0] == 0
                assert c.execute("SELECT version_num FROM alembic_version").fetchone()[0] == PREV_REVISION
            self._alembic(target_sa, "upgrade", REVISION)
        finally:
            with psycopg.connect(admin, autocommit=True) as c:
                c.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
