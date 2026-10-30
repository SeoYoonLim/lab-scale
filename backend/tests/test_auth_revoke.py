"""토큰 폐기(tv 클레임), POST /api/auth/logout, POST /api/auth/change-password (DB 없이 mock). 실제 DB 흐름은 test_auth_revoke_devdb.py.

가짜 사용자 저장소(FakeStore)가 deps.load_user, auth_api.revoke_tokens/change_password를 대신한다. 순수 함수
app.auth.change_password/revoke_tokens는 FakeSession으로 따로 검증한다.
"""

import logging
from datetime import datetime, timezone

import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

import app.api.auth as auth_api
import app.api.deps as deps
from app import auth
from app import rate_limit as RL
from app.auth import CurrentUser, InvalidToken, TokenRevoked, create_access_token, decode_token_claims
from app.main import app
from app.models import User

client = TestClient(app, raise_server_exceptions=False)

PASSWORD = "dummy-current-pass-1234"
NEW_PASSWORD = "dummy-new-pass-5678"
UNAUTHORIZED = {"detail": deps.UNAUTHORIZED_DETAIL}


def bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


class FakeStore:
    """id → [username, 비밀번호 평문, token_version]. 평문을 들고 있는 건 테스트 안에서만이다."""

    def __init__(self):
        self.rows = {3: ["alice", PASSWORD, 0], 4: ["bob", PASSWORD, 0]}

    def user(self, user_id: int) -> CurrentUser | None:
        row = self.rows.get(user_id)
        return CurrentUser(id=user_id, username=row[0], token_version=row[2]) if row else None

    def token(self, user_id: int) -> str:
        return create_access_token(user_id, token_version=self.rows[user_id][2])

    def revoke(self, db, user_id):
        if user_id not in self.rows:
            return None
        self.rows[user_id][2] += 1
        return self.rows[user_id][2]

    def change(self, db, user, current, new):
        row = self.rows.get(user.id)
        if row is None or row[2] != user.token_version:
            raise TokenRevoked()
        if current != row[1]:
            return None
        row[1] = new
        row[2] += 1
        return self.user(user.id)


@pytest.fixture
def store(monkeypatch):
    s = FakeStore()
    monkeypatch.setattr(deps, "load_user", s.user)
    monkeypatch.setattr(auth_api, "SessionLocal", lambda: type("S", (), {"close": lambda self: None})())
    monkeypatch.setattr(auth_api, "revoke_tokens", s.revoke)
    monkeypatch.setattr(auth_api, "change_password", s.change)
    monkeypatch.setattr(auth_api.recovery_db, "account_email", lambda db, user_id: (None, False))  # /me의 이메일 상태(2026-10-30)
    return s


def me(token: str):
    return client.get("/api/auth/me", headers=bearer(token))


def change(token: str, current=PASSWORD, new=NEW_PASSWORD):
    return client.post(
        "/api/auth/change-password", headers=bearer(token), json={"current_password": current, "new_password": new}
    )


# ---------- tv 클레임 ----------


class TestTvClaim:
    def test_new_token_carries_tv(self, jwt_secret):
        token = create_access_token(3, token_version=5)
        assert jwt.decode(token, jwt_secret, algorithms=["HS256"])["tv"] == 5
        assert decode_token_claims(token) == (3, 5)

    def test_token_without_tv_is_version_0(self, jwt_secret):
        """폐기 기능 이전에 발급된 토큰(sub/iat/exp만)."""
        now = datetime.now(timezone.utc)
        legacy = jwt.encode({"sub": "3", "iat": now, "exp": now + auth.ACCESS_TOKEN_TTL}, jwt_secret, algorithm="HS256")
        assert decode_token_claims(legacy) == (3, 0)

    @pytest.mark.parametrize("tv", [-1, "0", "1", 1.0, True, None, [0]])
    def test_malformed_tv_is_invalid(self, jwt_secret, tv):
        now = datetime.now(timezone.utc)
        claims = {"sub": "3", "iat": now, "exp": now + auth.ACCESS_TOKEN_TTL, "tv": tv}
        with pytest.raises(InvalidToken):
            decode_token_claims(jwt.encode(claims, jwt_secret, algorithm="HS256"))

    def test_matching_tv_passes(self, store):
        assert me(store.token(3)).status_code == 200

    def test_mismatching_tv_is_401(self, store):
        old = store.token(3)
        store.rows[3][2] = 1
        r = me(old)
        assert r.status_code == 401 and r.json() == UNAUTHORIZED
        assert r.headers["www-authenticate"] == "Bearer"

    def test_token_from_the_future_version_is_401(self, store):
        """DB보다 큰 tv(위조는 서명에서 막히지만, 다운그레이드·복원 뒤 남은 토큰)도 통과시키지 않는다."""
        assert me(create_access_token(3, token_version=7)).status_code == 401

    def test_legacy_token_works_until_first_revoke(self, store, jwt_secret):
        now = datetime.now(timezone.utc)
        legacy = jwt.encode({"sub": "3", "iat": now, "exp": now + auth.ACCESS_TOKEN_TTL}, jwt_secret, algorithm="HS256")
        assert me(legacy).status_code == 200
        assert client.post("/api/auth/logout", headers=bearer(legacy)).status_code == 200
        assert me(legacy).status_code == 401


# ---------- 로그아웃 ----------


class TestLogout:
    def test_logout_revokes_all_tokens_of_that_user_only(self, store):
        a1, a2, b = store.token(3), store.token(3), store.token(4)  # alice의 두 기기, bob
        r = client.post("/api/auth/logout", headers=bearer(a1))
        assert r.status_code == 200 and r.json() == {"ok": True}
        assert me(a1).status_code == 401
        assert me(a2).status_code == 401  # 다른 기기도(전체 기기 로그아웃)
        assert me(b).status_code == 200  # 다른 사용자는 영향 없음
        assert store.rows[3][2] == 1 and store.rows[4][2] == 0

    def test_logout_with_revoked_token_is_401_and_changes_nothing(self, store):
        token = store.token(3)
        client.post("/api/auth/logout", headers=bearer(token))
        r = client.post("/api/auth/logout", headers=bearer(token))
        assert r.status_code == 401 and r.json() == UNAUTHORIZED
        assert store.rows[3][2] == 1

    def test_logout_without_token_is_401(self, store):
        assert client.post("/api/auth/logout").status_code == 401

    def test_relogin_after_logout_gets_a_working_token(self, store):
        client.post("/api/auth/logout", headers=bearer(store.token(3)))
        assert me(store.token(3)).status_code == 200  # 새로 발급하면 새 tv

    def test_user_deleted_between_check_and_update_is_401(self, store, monkeypatch):
        monkeypatch.setattr(auth_api, "revoke_tokens", lambda db, user_id: None)
        assert client.post("/api/auth/logout", headers=bearer(store.token(3))).status_code == 401

    def test_db_error_is_503(self, store, monkeypatch):
        def boom(db, user_id):
            raise OperationalError("UPDATE users", {}, Exception("down"))

        monkeypatch.setattr(auth_api, "revoke_tokens", boom)
        assert client.post("/api/auth/logout", headers=bearer(store.token(3))).status_code == 503


# ---------- 비밀번호 변경 ----------


class TestChangePassword:
    def test_success_returns_new_token_and_revokes_old_ones(self, store):
        old, other_device = store.token(3), store.token(3)
        r = change(old)
        assert r.status_code == 200
        body = r.json()
        assert set(body) == {"user", "access_token", "token_type"}  # 로그인 응답과 같은 형식
        assert body["user"] == {"id": 3, "username": "alice"} and body["token_type"] == "bearer"
        assert me(body["access_token"]).status_code == 200  # 현재 세션은 새 토큰으로 이어진다
        assert me(old).status_code == 401
        assert me(other_device).status_code == 401
        assert store.rows[3][1] == NEW_PASSWORD
        assert me(store.token(4)).status_code == 200

    def test_wrong_current_password_is_422_and_changes_nothing(self, store):
        token = store.token(3)
        r = change(token, current="not-the-password")
        assert r.status_code == 422
        assert r.json() == {"detail": auth_api.CURRENT_PASSWORD_WRONG}
        assert "www-authenticate" not in r.headers  # 401이 아니므로 프론트가 로그인 상태를 지우지 않는다
        assert store.rows[3][1] == PASSWORD and store.rows[3][2] == 0
        assert me(token).status_code == 200

    @pytest.mark.parametrize("new", ["short7!", "p" * 129, "", None, 12345678])
    def test_new_password_rule_violation_is_422_without_echo(self, store, monkeypatch, new):
        monkeypatch.setattr(auth_api, "change_password", lambda *a: pytest.fail("호출되면 안 됨"))
        r = change(store.token(3), new=new)
        assert r.status_code == 422
        for err in r.json()["detail"]:
            assert "input" not in err
        assert PASSWORD not in r.text
        if isinstance(new, str) and new:
            assert new not in r.text

    def test_same_password_is_rejected(self, store, monkeypatch):
        monkeypatch.setattr(auth_api, "change_password", lambda *a: pytest.fail("호출되면 안 됨"))
        r = change(store.token(3), new=PASSWORD)
        assert r.status_code == 422
        assert [e["type"] for e in r.json()["detail"]] == ["same_password"]
        assert PASSWORD not in r.text

    @pytest.mark.parametrize("body", [{}, {"new_password": NEW_PASSWORD}, {"current_password": "", "new_password": NEW_PASSWORD},
                                      {"current_password": "p" * 129, "new_password": NEW_PASSWORD}])
    def test_malformed_body_is_422_without_echo(self, store, body):
        r = client.post("/api/auth/change-password", headers=bearer(store.token(3)), json=body)
        assert r.status_code == 422
        assert NEW_PASSWORD not in r.text

    def test_without_valid_token_is_401_before_body_is_looked_at(self, store):
        assert client.post("/api/auth/change-password", json={}).status_code == 401
        old = store.token(3)
        store.rows[3][2] = 1
        r = change(old)
        assert r.status_code == 401 and r.json() == UNAUTHORIZED
        assert store.rows[3][1] == PASSWORD

    def test_revoked_during_change_is_401(self, store, monkeypatch):
        def raced(db, user, current, new):
            raise TokenRevoked()

        monkeypatch.setattr(auth_api, "change_password", raced)
        assert change(store.token(3)).status_code == 401

    def test_db_error_is_503_and_logs_no_secret(self, store, monkeypatch, caplog):
        def boom(db, user, current, new):
            raise OperationalError("UPDATE users SET password_hash", {"password_hash": "$argon2id$leak"}, Exception("down"))

        monkeypatch.setattr(auth_api, "change_password", boom)
        with caplog.at_level(logging.DEBUG):
            r = change(store.token(3))
        assert r.status_code == 503
        for text in (r.text, caplog.text):
            assert "argon2" not in text and PASSWORD not in text and NEW_PASSWORD not in text

    def test_passwords_never_in_responses_or_logs(self, store, caplog):
        with caplog.at_level(logging.DEBUG):
            responses = [
                change(store.token(3), current="wrong-password-x"),
                change(store.token(3), new=PASSWORD),
                change(store.token(3), new="short"),
                change(store.token(3)),
            ]
        assert [r.status_code for r in responses] == [422, 422, 422, 200]
        for text in [r.text for r in responses] + [caplog.text]:
            for secret in (PASSWORD, NEW_PASSWORD, "wrong-password-x"):
                assert secret not in text


# ---------- 레이트 리밋(시계 주입) ----------


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


@pytest.fixture
def limited(monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "1")
    clock = Clock()
    monkeypatch.setattr(RL, "logout", RL.SlidingWindow(60, clock=clock))
    monkeypatch.setattr(RL, "password_change", RL.SlidingWindow(3600, clock=clock))
    monkeypatch.setattr(RL, "password_change_failures", RL.FailureLock(clock=clock))
    return clock


class TestRateLimits:
    def test_wrong_current_password_locks_after_threshold(self, store, limited, monkeypatch):
        monkeypatch.setenv("PASSWORD_CHANGE_LOCK_FAILURES", "3")
        monkeypatch.setenv("PASSWORD_CHANGE_LOCK_SECONDS", "300")
        token = store.token(3)
        assert [change(token, current="wrong-password-x").status_code for _ in range(3)] == [422, 422, 422]
        locked = change(token)  # 맞는 비밀번호여도 잠금 중이면 429
        assert locked.status_code == 429 and locked.headers["retry-after"] == "300"
        assert store.rows[3][1] == PASSWORD
        assert change(store.token(4), current="wrong-password-x").status_code == 422  # 다른 사용자는 따로 센다
        limited.t += 301
        assert change(token).status_code == 200

    def test_success_resets_failure_count(self, store, limited, monkeypatch):
        monkeypatch.setenv("PASSWORD_CHANGE_LOCK_FAILURES", "2")
        assert change(store.token(3), current="wrong-password-x").status_code == 422
        assert change(store.token(3)).status_code == 200
        r = change(store.token(3), current="wrong-password-x", new="another-pass-999")
        assert r.status_code == 422  # 카운트가 0부터 다시 시작해서 아직 잠기지 않는다
        assert change(store.token(3), current=NEW_PASSWORD, new="another-pass-999").status_code == 200

    def test_rule_violations_do_not_count_as_failures(self, store, limited, monkeypatch):
        monkeypatch.setenv("PASSWORD_CHANGE_LOCK_FAILURES", "2")
        for _ in range(5):
            assert change(store.token(3), new="short").status_code == 422
        assert change(store.token(3)).status_code == 200

    def test_password_change_attempts_per_hour(self, store, limited, monkeypatch):
        monkeypatch.setenv("RATE_LIMIT_PASSWORD_CHANGE_PER_HOUR", "2")
        monkeypatch.setenv("PASSWORD_CHANGE_LOCK_FAILURES", "100")
        token = store.token(3)
        assert [change(token, current="wrong-password-x").status_code for _ in range(2)] == [422, 422]
        r = change(token)
        assert r.status_code == 429 and r.headers["retry-after"] == "3600"
        limited.t += 3600
        assert change(token).status_code == 200

    def test_logout_per_minute(self, store, limited, monkeypatch):
        monkeypatch.setenv("RATE_LIMIT_LOGOUT_PER_MIN", "2")
        for _ in range(2):
            assert client.post("/api/auth/logout", headers=bearer(store.token(3))).status_code == 200
        r = client.post("/api/auth/logout", headers=bearer(store.token(3)))
        assert r.status_code == 429 and r.headers["retry-after"] == "60"
        assert store.rows[3][2] == 2  # 429면 폐기하지 않는다
        assert client.post("/api/auth/logout", headers=bearer(store.token(4))).status_code == 200
        limited.t += 60
        assert client.post("/api/auth/logout", headers=bearer(store.token(3))).status_code == 200

    def test_defaults_are_documented_constants(self):
        assert RL.DEFAULTS["RATE_LIMIT_LOGOUT_PER_MIN"] == 10
        assert RL.DEFAULTS["RATE_LIMIT_PASSWORD_CHANGE_PER_HOUR"] == 10
        assert RL.DEFAULTS["PASSWORD_CHANGE_LOCK_FAILURES"] == 5
        assert RL.DEFAULTS["PASSWORD_CHANGE_LOCK_SECONDS"] == 300


# ---------- 순수 함수(app.auth) ----------


class FakeResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class FakeSession:
    """change_password/revoke_tokens가 쓰는 execute().scalar_one_or_none(), commit, rollback만 흉내낸다."""

    def __init__(self, value):
        self.value, self.commits, self.rollbacks, self.statements = value, 0, 0, []

    def execute(self, stmt):
        self.statements.append(stmt)
        return FakeResult(self.value)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def _row(version=0, password=PASSWORD):
    return User(id=3, username="alice", password_hash=auth.hash_password(password), token_version=version)


class TestPureFunctions:
    def test_change_password_success(self):
        row = _row(version=2)
        db = FakeSession(row)
        user = auth.change_password(db, CurrentUser(3, "alice", 2), PASSWORD, NEW_PASSWORD)
        assert user == CurrentUser(3, "alice", 3)
        assert db.commits == 1
        assert auth.verify_password(NEW_PASSWORD, row.password_hash)
        assert NEW_PASSWORD not in row.password_hash and row.password_hash.startswith("$argon2id$")
        assert "FOR UPDATE" in str(db.statements[0].compile())

    def test_change_password_wrong_current_changes_nothing(self):
        row = _row()
        before = row.password_hash
        db = FakeSession(row)
        assert auth.change_password(db, CurrentUser(3, "alice", 0), "wrong-password-x", NEW_PASSWORD) is None
        assert row.password_hash == before and row.token_version == 0
        assert db.commits == 0 and db.rollbacks == 1

    def test_change_password_with_stale_version_raises(self):
        db = FakeSession(_row(version=1))
        with pytest.raises(TokenRevoked):
            auth.change_password(db, CurrentUser(3, "alice", 0), PASSWORD, NEW_PASSWORD)
        assert db.commits == 0

    def test_change_password_for_deleted_user_raises(self):
        with pytest.raises(TokenRevoked):
            auth.change_password(FakeSession(None), CurrentUser(3, "alice", 0), PASSWORD, NEW_PASSWORD)

    def test_revoke_tokens_increments_in_sql(self):
        db = FakeSession(4)
        assert auth.revoke_tokens(db, 3) == 4
        sql = str(db.statements[0].compile())
        assert sql.startswith("UPDATE users SET token_version=(users.token_version +")  # 읽고 쓰기가 아니라 한 문장
        assert "RETURNING" in sql and db.commits == 1

    def test_user_repr_has_no_hash(self):
        assert "argon2" not in repr(_row())
