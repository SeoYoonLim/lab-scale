"""계정 복구 라우트 테스트(DB 없이). DB 계층(app.account_recovery_db)을 가짜로 바꿔서 인증 순서(401이 422보다 먼저),
검증 오류에 입력이 되돌아오지 않는지, 열거 방지(같은 상태 코드·같은 본문), 레이트 리밋을 켠 상태의 한도와 Retry-After를 확인한다.
실제 DB로 하는 흐름·토큰 보안 검증은 test_account_recovery_devdb.py."""

import secrets

import pytest
from fastapi.testclient import TestClient

import app.api.auth as auth_api
from app import rate_limit
from app.account_recovery import INVALID_LINK, RECOVERY_ACCEPTED
from app.account_recovery_db import EmailChange
from app.auth import CurrentUser, EmailTaken, PasswordNotSet, TokenRevoked
from app.main import app

client = TestClient(app, raise_server_exceptions=False)

DUMMY_PASSWORD = "dummy-" + secrets.token_urlsafe(10)
DUMMY_TOKEN = "dummy-token-" + secrets.token_urlsafe(16)


class _NoDB:
    def close(self):
        pass


@pytest.fixture
def fake_db(monkeypatch):
    """라우트가 SessionLocal을 열어도 DB에 붙지 않게 하고, DB 계층 호출을 기록한다."""
    calls = []
    monkeypatch.setattr(auth_api, "SessionLocal", _NoDB)
    rdb = auth_api.recovery_db

    def rec(name, result):
        def f(*a, **k):
            calls.append((name, a[1:] if a and isinstance(a[0], _NoDB) else a))
            return result(*a) if callable(result) else result
        return f

    monkeypatch.setattr(rdb, "deliver_username", rec("deliver_username", None))
    monkeypatch.setattr(rdb, "deliver_reset", rec("deliver_reset", None))
    monkeypatch.setattr(rdb, "verify_email", rec("verify_email", False))
    monkeypatch.setattr(rdb, "reset_password", rec("reset_password", None))
    monkeypatch.setattr(rdb, "new_verification", rec("new_verification", ("sent", "raw-dummy", "a@b.com", "tester")))
    monkeypatch.setattr(rdb, "set_email", rec("set_email", EmailChange("changed", "raw-dummy", "tester", None)))
    monkeypatch.setattr(rdb, "send_email_changed_notice", rec("send_email_changed_notice", None))
    monkeypatch.setattr(rdb, "account_email", rec("account_email", ("a@b.com", False)))
    monkeypatch.setattr(rdb, "token_owner", lambda *a: None)  # 재설정 전 비밀번호 문맥 검사용 조회(2026-10-30)
    monkeypatch.setattr(rdb, "send_verification", rec("send_verification", None))
    return calls


@pytest.fixture
def on(monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "1")
    rate_limit.reset_all()


def names(calls):
    return [c[0] for c in calls]


class TestAuthOrder:
    @pytest.mark.parametrize("method, path", [("PUT", "/api/auth/email"), ("POST", "/api/auth/resend-verification")])
    def test_401_without_token(self, fake_db, method, path):
        r = client.request(method, path, json={"email": "a@b.com"})
        assert r.status_code == 401 and r.headers["www-authenticate"] == "Bearer"
        assert fake_db == []

    def test_401_comes_before_422(self, fake_db):
        assert client.put("/api/auth/email", json={"email": "not-an-email"}).status_code == 401
        assert client.put("/api/auth/email", json={}).status_code == 401
        assert fake_db == []


class TestValidation:
    @pytest.mark.parametrize("path", ["/api/auth/find-username", "/api/auth/forgot-password"])
    @pytest.mark.parametrize("email", ["not-an-email-dummy", "a@b", "x" * 400 + "@b.com", ""])
    def test_bad_email_is_422_without_echo(self, fake_db, path, email):
        r = client.post(path, json={"email": email})
        assert r.status_code == 422 and fake_db == []
        if email:
            assert email not in r.text and '"input"' not in r.text

    def test_bad_email_on_put_and_signup(self, fake_db, login_as):
        login_as(7)
        r = client.put("/api/auth/email", json={"email": "bad-email-dummy", "current_password": DUMMY_PASSWORD})
        assert r.status_code == 422 and "bad-email-dummy" not in r.text
        r = client.post("/api/auth/signup", json={"username": "alice", "password": DUMMY_PASSWORD, "email": "bad-email-dummy"})
        assert r.status_code == 422 and "bad-email-dummy" not in r.text and DUMMY_PASSWORD not in r.text
        assert fake_db == []

    @pytest.mark.parametrize("pw", ["short", "x" * 129])
    def test_reset_password_policy_is_422_before_touching_the_token(self, fake_db, pw):
        r = client.post("/api/auth/reset-password", json={"token": DUMMY_TOKEN, "new_password": pw})
        assert r.status_code == 422 and fake_db == [] and DUMMY_TOKEN not in r.text and pw not in r.text

    @pytest.mark.parametrize("body", [{}, {"token": ""}, {"token": "x" * 129}])
    def test_token_shape_is_422(self, fake_db, body):
        assert client.post("/api/auth/verify-email", json=body).status_code == 422 and fake_db == []


class TestEnumerationResistance:
    @pytest.mark.parametrize("path, job", [("/api/auth/find-username", "deliver_username"), ("/api/auth/forgot-password", "deliver_reset")])
    def test_same_status_and_body_for_any_email(self, fake_db, path, job):
        rs = [client.post(path, json={"email": e}) for e in ("known@example.com", "nobody@example.com", "UNVERIFIED@Example.com ")]
        assert {r.status_code for r in rs} == {200}
        assert len({r.text for r in rs}) == 1 and rs[0].json() == {"ok": True, "message": RECOVERY_ACCEPTED}
        assert {tuple(sorted((k, v) for k, v in r.headers.items() if k.lower() not in ("date", "x-request-id"))) for r in rs}.__len__() == 1
        # 조회는 모두 응답 뒤 작업으로 넘어가고, 이메일은 정규화돼 전달된다
        assert fake_db == [(job, ("known@example.com",)), (job, ("nobody@example.com",)), (job, ("unverified@example.com",))]

    def test_response_never_contains_a_username(self, fake_db):
        r = client.post("/api/auth/find-username", json={"email": "known@example.com"})
        assert "username" not in r.text

    def test_invalid_token_detail_is_one_message(self, fake_db):
        for path, body in [("/api/auth/verify-email", {"token": DUMMY_TOKEN}),
                           ("/api/auth/reset-password", {"token": DUMMY_TOKEN, "new_password": DUMMY_PASSWORD})]:
            r = client.post(path, json=body)
            assert r.status_code == 400 and r.json() == {"detail": INVALID_LINK}


class TestSignupCompatibility:
    def test_signup_without_email_calls_create_user_as_before(self, monkeypatch, fake_db, mail_outbox):
        seen = []
        monkeypatch.setattr(auth_api, "create_user", lambda *a, **k: seen.append((a[1:], k)) or CurrentUser(5, "alice"))
        r = client.post("/api/auth/signup", json={"username": "Alice", "password": DUMMY_PASSWORD})
        assert r.status_code == 201 and set(r.json()) == {"user", "access_token", "token_type"}
        assert r.json()["user"] == {"id": 5, "username": "alice"}
        assert seen == [(("alice", DUMMY_PASSWORD), {})] and fake_db == []

    def test_signup_with_email_sends_verification_and_keeps_response_shape(self, monkeypatch, fake_db):
        seen = []
        monkeypatch.setattr(auth_api, "create_user", lambda *a, **k: seen.append(k) or CurrentUser(5, "alice"))
        r = client.post("/api/auth/signup", json={"username": "alice", "password": DUMMY_PASSWORD, "email": " Alice@Example.com"})
        assert r.status_code == 201 and set(r.json()) == {"user", "access_token", "token_type"} and r.json()["user"] == {"id": 5, "username": "alice"}
        assert seen == [{"email": "alice@example.com"}]
        assert names(fake_db) == ["new_verification", "send_verification"]
        assert fake_db[1][1] == ("alice@example.com", "alice", "raw-dummy")

    def test_signup_email_conflict_is_409(self, monkeypatch, fake_db):
        def taken(*a, **k):
            raise EmailTaken()
        monkeypatch.setattr(auth_api, "create_user", taken)
        r = client.post("/api/auth/signup", json={"username": "alice", "password": DUMMY_PASSWORD, "email": "a@b.com"})
        assert r.status_code == 409 and r.json() == {"detail": "이미 사용 중인 이메일입니다."}


class TestEmailRoutes:
    def test_put_email_sends_verification(self, fake_db, login_as):
        login_as(7)
        r = client.put("/api/auth/email", json={"email": "New@Example.com", "current_password": DUMMY_PASSWORD})
        assert r.status_code == 200 and r.json()["verification_sent"] is True
        name, args = fake_db[0]
        assert name == "set_email" and args[0].id == 7 and args[1:3] == ("new@example.com", DUMMY_PASSWORD)
        assert ("send_verification", ("new@example.com", "tester", "raw-dummy")) in fake_db

    def test_same_email_sends_nothing(self, monkeypatch, fake_db, login_as):
        login_as(7)
        monkeypatch.setattr(auth_api.recovery_db, "set_email", lambda *a: EmailChange("unchanged", username="tester"))
        r = client.put("/api/auth/email", json={"email": "a@b.com", "current_password": DUMMY_PASSWORD})
        assert r.status_code == 200 and r.json()["verification_sent"] is False and "send_verification" not in names(fake_db)

    @pytest.mark.parametrize("status, detail", [("no_email", "등록된 이메일이 없습니다"), ("verified", "이미 인증된 이메일입니다")])
    def test_resend_400_for_own_state(self, monkeypatch, fake_db, login_as, status, detail):
        login_as(7)
        monkeypatch.setattr(auth_api.recovery_db, "new_verification", lambda *a: (status, None, None, None))
        r = client.post("/api/auth/resend-verification")
        assert r.status_code == 400 and detail in r.json()["detail"]


class TestRateLimitsOn:
    def test_find_username_per_ip_per_minute(self, on, fake_db):
        for i in range(5):
            assert client.post("/api/auth/find-username", json={"email": f"u{i}@example.com"}).status_code == 200
        r = client.post("/api/auth/find-username", json={"email": "u9@example.com"})
        assert r.status_code == 429 and int(r.headers["retry-after"]) >= 1 and "요청이 너무 많습니다" in r.json()["detail"]
        # 엔드포인트별로 따로 센다
        assert client.post("/api/auth/forgot-password", json={"email": "u0@example.com"}).status_code == 200

    def test_per_email_per_hour(self, on, fake_db, monkeypatch):
        monkeypatch.setenv("RATE_LIMIT_RECOVERY_PER_MIN", "100")
        for _ in range(3):
            assert client.post("/api/auth/forgot-password", json={"email": "victim@example.com"}).status_code == 200
        r = client.post("/api/auth/forgot-password", json={"email": " VICTIM@example.com"})  # 정규화된 같은 이메일
        assert r.status_code == 429 and int(r.headers["retry-after"]) > 60
        assert client.post("/api/auth/forgot-password", json={"email": "other@example.com"}).status_code == 200
        assert names(fake_db).count("deliver_reset") == 4  # 막힌 요청은 메일 작업을 만들지 않는다

    @pytest.mark.parametrize("path, body", [
        ("/api/auth/verify-email", {"token": DUMMY_TOKEN}),
        ("/api/auth/reset-password", {"token": DUMMY_TOKEN, "new_password": DUMMY_PASSWORD}),
    ])
    def test_token_endpoints_per_ip_per_minute(self, on, fake_db, path, body):
        for _ in range(10):
            assert client.post(path, json=body).status_code == 400
        r = client.post(path, json=body)
        assert r.status_code == 429 and "retry-after" in r.headers

    def test_verification_mail_per_user_shared_by_put_and_resend(self, on, fake_db, login_as):
        login_as(7)
        for _ in range(3):
            assert client.put("/api/auth/email", json={"email": "a@b.com", "current_password": DUMMY_PASSWORD}).status_code == 200
        for _ in range(2):
            assert client.post("/api/auth/resend-verification").status_code == 200
        assert client.post("/api/auth/resend-verification").status_code == 429
        assert client.put("/api/auth/email", json={"email": "c@d.com", "current_password": DUMMY_PASSWORD}).status_code == 429
        login_as(8)  # 다른 사용자는 따로 센다
        assert client.post("/api/auth/resend-verification").status_code == 200

    def test_defaults(self):
        assert [rate_limit.DEFAULTS[k] for k in ("RATE_LIMIT_RECOVERY_PER_MIN", "RATE_LIMIT_RECOVERY_EMAIL_PER_HOUR",
                                                 "RATE_LIMIT_TOKEN_PER_MIN", "RATE_LIMIT_VERIFICATION_MAIL_PER_HOUR")] == [5, 3, 10, 5]

    def test_reset_clears_login_locks_for_that_username_only(self, on, monkeypatch):
        for _ in range(5):
            rate_limit.record_login("alice|1.1.1.1", success=False)
            rate_limit.record_login("alice|2.2.2.2", success=False)
            rate_limit.record_login("alicex|1.1.1.1", success=False)
        assert rate_limit.login_failures.locked_for("alice|1.1.1.1") > 0
        rate_limit.clear_locks_after_password_reset("alice", "user:1")
        assert rate_limit.login_failures.locked_for("alice|1.1.1.1") == 0 == rate_limit.login_failures.locked_for("alice|2.2.2.2")
        assert rate_limit.login_failures.locked_for("alicex|1.1.1.1") > 0


class TestEmailChangeHardening:
    @pytest.fixture(autouse=True)
    def _in(self, login_as):
        login_as(7)

    @pytest.mark.parametrize("body", [{"email": "a@b.com"}, {"email": "a@b.com", "current_password": ""},
                                      {"email": "a@b.com", "current_password": "x" * 129}])
    def test_current_password_is_required(self, fake_db, body):
        r = client.put("/api/auth/email", json=body)
        assert r.status_code == 422 and fake_db == [] and '"input"' not in r.text

    def test_wrong_password_is_422_with_change_password_format(self, monkeypatch, fake_db):
        monkeypatch.setattr(auth_api.recovery_db, "set_email", lambda *a: EmailChange("wrong_password"))
        r = client.put("/api/auth/email", json={"email": "a@b.com", "current_password": DUMMY_PASSWORD})
        assert r.status_code == 422 and r.json() == {"detail": auth_api.CURRENT_PASSWORD_WRONG}
        assert DUMMY_PASSWORD not in r.text and "send_verification" not in names(fake_db)

    def test_account_without_password_is_400(self, monkeypatch, fake_db):
        def no_pw(*a):
            raise PasswordNotSet()
        monkeypatch.setattr(auth_api.recovery_db, "set_email", no_pw)
        r = client.put("/api/auth/email", json={"email": "a@b.com", "current_password": DUMMY_PASSWORD})
        assert r.status_code == 400 and r.json() == {"detail": auth_api.NO_PASSWORD_DETAIL} and "소셜 로그인" in r.json()["detail"]

    def test_revoked_between_auth_and_update_is_401(self, monkeypatch, fake_db):
        def revoked(*a):
            raise TokenRevoked()
        monkeypatch.setattr(auth_api.recovery_db, "set_email", revoked)
        assert client.put("/api/auth/email", json={"email": "a@b.com", "current_password": DUMMY_PASSWORD}).status_code == 401

    def test_notice_goes_to_previous_verified_address(self, monkeypatch, fake_db):
        monkeypatch.setattr(auth_api.recovery_db, "set_email", lambda *a: EmailChange("changed", "raw-dummy", "tester", "old@example.com"))
        r = client.put("/api/auth/email", json={"email": "New@example.com", "current_password": DUMMY_PASSWORD})
        assert r.status_code == 200
        assert ("send_email_changed_notice", ("old@example.com", "tester", "new@example.com")) in fake_db
        assert ("send_verification", ("new@example.com", "tester", "raw-dummy")) in fake_db

    def test_failures_feed_the_shared_password_lock(self, on, monkeypatch, fake_db):
        monkeypatch.setenv("RATE_LIMIT_VERIFICATION_MAIL_PER_HOUR", "100")
        monkeypatch.setattr(auth_api.recovery_db, "set_email", lambda *a: EmailChange("wrong_password"))
        for _ in range(5):
            assert client.put("/api/auth/email", json={"email": "a@b.com", "current_password": DUMMY_PASSWORD}).status_code == 422
        r = client.put("/api/auth/email", json={"email": "a@b.com", "current_password": DUMMY_PASSWORD})
        assert r.status_code == 429 and "retry-after" in r.headers
        assert rate_limit.password_change_failures.locked_for("user:7") > 0

    def test_resend_does_not_need_a_password(self, fake_db):
        assert client.post("/api/auth/resend-verification").status_code == 200

