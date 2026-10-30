"""소셜 로그인 라우트 테스트(DB 없음: app.oauth_db를 가짜로, 공급자 HTTP도 가짜). 리디렉션·쿠키 속성, state·쿠키 오류,
동의 거부, 공급자 실패, 키 미설정, providers 목록에 비밀값 없음, exchange 응답 형식, 레이트 리밋 켠 상태를 본다.
실제 DB로 계정 생성·연결·1회용 코드는 test_oauth_devdb.py."""

import logging
from datetime import timedelta
from urllib.parse import urlsplit

import pytest
import requests
from fastapi.testclient import TestClient

import app.api.oauth as oauth_api
from app import oauth, oauth_providers, rate_limit
from app.auth import CurrentUser
from app.main import app
from oauth_fakes import (GOOGLE_ID_DUMMY, GOOGLE_SECRET_DUMMY, NAVER_ID_DUMMY, NAVER_SECRET_DUMMY, FakeProvider, callback,
                         code_from, configure, google_claims, start)

FRONT = "http://localhost:5173"


class _NoDB:
    def close(self):
        pass

    def rollback(self):
        pass


@pytest.fixture
def fake(monkeypatch):
    f = FakeProvider()
    monkeypatch.setattr(oauth_providers, "HTTP", f)
    return f


@pytest.fixture
def fake_db(monkeypatch):
    seen = {"resolved": [], "codes": []}
    monkeypatch.setattr(oauth_api, "SessionLocal", _NoDB)

    def resolve(db, profile, at):
        seen["resolved"].append(profile)
        return CurrentUser(id=42, username=f"{profile.provider}_0a1b2c3d"), oauth.Decision("create")

    def issue(db, user_id, at):
        seen["codes"].append(user_id)
        return "dummy-one-time-code-" + "x" * 20

    monkeypatch.setattr(oauth_api.oauth_db, "resolve_login", resolve)
    monkeypatch.setattr(oauth_api.oauth_db, "issue_login_code", issue)
    monkeypatch.setattr(oauth_api.oauth_db, "exchange_login_code",
                        lambda db, raw, at: CurrentUser(id=42, username="google_0a1b2c3d") if raw == "good-dummy-code" else None)
    return seen


@pytest.fixture
def client():
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def on(monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "1")
    rate_limit.reset_all()


def is_error(resp, kind):
    return resp.status_code == 302 and resp.headers["location"] == f"{FRONT}/login?error={kind}"


class TestProviders:
    def test_lists_only_configured_and_never_keys(self, client, monkeypatch):
        assert client.get("/api/auth/oauth/providers").json() == {"providers": []}
        configure(monkeypatch, naver=False)
        assert [p["id"] for p in client.get("/api/auth/oauth/providers").json()["providers"]] == ["google"]
        configure(monkeypatch)
        r = client.get("/api/auth/oauth/providers")
        assert r.json() == {"providers": [
            {"id": "google", "name": "Google", "login_path": "/api/auth/oauth/google/login"},
            {"id": "naver", "name": "네이버", "login_path": "/api/auth/oauth/naver/login"}]}
        for v in (GOOGLE_ID_DUMMY, GOOGLE_SECRET_DUMMY, NAVER_ID_DUMMY, NAVER_SECRET_DUMMY, "secret"):
            assert v not in r.text


class TestLogin:
    def test_unconfigured_provider_goes_to_unavailable_page(self, client):
        r = client.get("/api/auth/oauth/google/login", follow_redirects=False)
        assert is_error(r, "oauth_unavailable") and "oauth_state=" not in r.headers.get("set-cookie", "").replace("oauth_state=\"\"", "")

    def test_unknown_provider_is_404(self, client):
        assert client.get("/api/auth/oauth/kakao/login", follow_redirects=False).status_code == 404

    def test_redirect_and_cookie_attributes(self, client, monkeypatch):
        configure(monkeypatch)
        r, q, state, nonce = start(client, "google")
        assert r.headers["location"].startswith("https://accounts.google.com/o/oauth2/v2/auth?")
        assert r.headers["cache-control"] == "no-store" and r.headers["referrer-policy"] == "no-referrer"
        cookie = r.headers["set-cookie"]
        assert cookie.startswith("oauth_state=") and "HttpOnly" in cookie and "SameSite=lax" in cookie
        assert "Path=/api/auth/oauth" in cookie and "Max-Age=600" in cookie and "Secure" not in cookie
        assert q["code_challenge_method"] == "S256" and q["nonce"] == nonce and GOOGLE_SECRET_DUMMY not in r.headers["location"]

    def test_https_backend_sets_secure_cookie(self, client, monkeypatch):
        configure(monkeypatch)
        monkeypatch.setenv("OAUTH_REDIRECT_BASE", "https://api.example.com")
        r = client.get("/api/auth/oauth/naver/login", follow_redirects=False)
        assert "Secure" in r.headers["set-cookie"]
        assert "redirect_uri=https%3A%2F%2Fapi.example.com%2Fapi%2Fauth%2Foauth%2Fnaver%2Fcallback" in r.headers["location"]


class TestCallback:
    @pytest.fixture(autouse=True)
    def _setup(self, monkeypatch, fake, fake_db):
        configure(monkeypatch)

    def test_success_redirects_with_fragment_code_and_clears_cookie(self, client, fake, fake_db):
        _, _, state, nonce = start(client, "google")
        fake.google_claims = google_claims(nonce=nonce)
        r = callback(client, "google", state)
        assert r.status_code == 302 and code_from(r.headers["location"]).startswith("dummy-one-time-code-")
        assert r.headers["location"].startswith(f"{FRONT}/oauth/callback#code=")
        assert r.headers["cache-control"] == "no-store" and r.headers["referrer-policy"] == "no-referrer"
        assert 'oauth_state=""' in r.headers["set-cookie"] and "Max-Age=0" in r.headers["set-cookie"]
        assert fake_db["resolved"][0].provider_user_id == "g-123" and fake_db["codes"] == [42]
        # PKCE verifier가 토큰 요청에만 실렸다
        assert len(fake.calls[0][2]["code_verifier"]) >= 43

    def test_naver_success(self, client, fake, fake_db):
        _, _, state, _ = start(client, "naver")
        r = callback(client, "naver", state)
        assert r.headers["location"].startswith(f"{FRONT}/oauth/callback#code=") and fake_db["resolved"][0].email == "user@naver.com"

    def test_state_from_another_login_is_rejected(self, client, fake, fake_db):
        _, _, attacker_state, _ = start(TestClient(app), "google")  # 공격자가 자기 브라우저에서 받은 state
        start(client, "google")  # 피해자 브라우저에는 다른 nonce 쿠키
        r = callback(client, "google", attacker_state)
        assert is_error(r, "oauth_failed") and fake.calls == [] and fake_db["resolved"] == []

    def test_missing_cookie(self, client, fake, fake_db):
        _, _, state, _ = start(client, "google")
        r = callback(TestClient(app), "google", state)  # 쿠키 없는 브라우저(로그인 CSRF)
        assert is_error(r, "oauth_failed") and fake.calls == []

    def test_expired_state(self, client, fake, monkeypatch):
        _, _, state, nonce = start(client, "google")
        real_now = oauth.now
        monkeypatch.setattr(oauth, "now", lambda: real_now() + timedelta(minutes=10, seconds=1))
        assert is_error(callback(client, "google", state), "oauth_failed") and fake.calls == []

    def test_tampered_state(self, client, fake):
        _, _, state, _ = start(client, "google")
        assert is_error(callback(client, "google", state[:-3] + "AAA"), "oauth_failed")

    def test_provider_swap(self, client, fake):
        _, _, state, _ = start(client, "naver")
        assert is_error(callback(client, "google", state), "oauth_failed")

    @pytest.mark.parametrize("params", [{}, {"state": "x"}, {"code": "c"}, {"code": "c" * 3000, "state": "s"}])
    def test_missing_or_oversized_params(self, client, params):
        r = client.get("/api/auth/oauth/google/callback", params=params, follow_redirects=False)
        assert is_error(r, "oauth_failed")

    def test_user_denied(self, client, fake):
        _, _, state, _ = start(client, "naver")
        r = client.get("/api/auth/oauth/naver/callback", params={"error": "access_denied", "error_description": "Canceled By User",
                                                                 "state": state}, follow_redirects=False)
        assert is_error(r, "oauth_denied") and fake.calls == []
        r = client.get("/api/auth/oauth/google/callback", params={"error": "server_error"}, follow_redirects=False)
        assert is_error(r, "oauth_failed")

    @pytest.mark.parametrize("err", [requests.Timeout(f"read timeout secret={GOOGLE_SECRET_DUMMY}"), requests.ConnectionError("x"),
                                     "bad_aud"])
    def test_provider_failure_is_generic_and_leaks_nothing(self, client, fake, fake_db, caplog, err):
        _, _, state, nonce = start(client, "google")
        if err == "bad_aud":
            fake.google_claims = google_claims(nonce=nonce, aud="other")
        else:
            fake.google_token_error = err
        with caplog.at_level(logging.DEBUG):
            r = callback(client, "google", state, code="dummy-provider-code-zz")
        assert is_error(r, "oauth_failed") and fake_db["resolved"] == []
        # 앱 로그(app.*)만 본다. TestClient(httpx)가 DEBUG로 남기는 요청 URL은 실서버의 uvicorn 접근 로그와 같은 것이라 API.md 한계에 적었다.
        app_log = "\n".join(rec.getMessage() for rec in caplog.records if rec.name.startswith("app"))
        for secret in (GOOGLE_SECRET_DUMMY, "dummy-provider-code-zz", state, "dummy-google-access"):
            assert secret not in app_log and secret not in r.headers["location"]
        assert "reason=" in app_log

    def test_db_failure_is_oauth_failed(self, client, fake, monkeypatch, caplog):
        def boom(db, profile, at):
            raise RuntimeError(f"insert failed for {profile.email}")
        monkeypatch.setattr(oauth_api.oauth_db, "resolve_login", boom)
        _, _, state, nonce = start(client, "google")
        fake.google_claims = google_claims(nonce=nonce)
        with caplog.at_level(logging.WARNING):
            r = callback(client, "google", state)
        assert is_error(r, "oauth_failed") and "user@gmail.com" not in caplog.text and "RuntimeError" in caplog.text

    def test_unconfigured_callback(self, client, monkeypatch):
        monkeypatch.delenv("GOOGLE_CLIENT_SECRET")
        assert is_error(client.get("/api/auth/oauth/google/callback", params={"code": "c", "state": "s"}, follow_redirects=False),
                        "oauth_unavailable")


class TestExchange:
    def test_success_has_login_shape(self, client, fake_db):
        r = client.post("/api/auth/oauth/exchange", json={"code": "good-dummy-code"})
        assert r.status_code == 200 and set(r.json()) == {"user", "access_token", "token_type"}
        assert r.json()["user"] == {"id": 42, "username": "google_0a1b2c3d"} and r.json()["token_type"] == "bearer"

    def test_bad_code_is_400_generic(self, client, fake_db):
        r = client.post("/api/auth/oauth/exchange", json={"code": "nope-dummy"})
        assert r.status_code == 400 and r.json() == {"detail": oauth_api.INVALID_CODE}

    @pytest.mark.parametrize("body", [{}, {"code": ""}, {"code": "x" * 129}])
    def test_validation_is_422_without_echo(self, client, fake_db, body):
        r = client.post("/api/auth/oauth/exchange", json=body)
        assert r.status_code == 422 and '"input"' not in r.text


class TestRateLimitsOn:
    def test_login_per_ip_per_minute(self, client, on, monkeypatch):
        configure(monkeypatch)
        for _ in range(20):
            assert client.get("/api/auth/oauth/google/login", follow_redirects=False).status_code == 302
        r = client.get("/api/auth/oauth/naver/login", follow_redirects=False)  # 공급자와 무관하게 IP 기준
        assert r.status_code == 429 and "retry-after" in r.headers
        # 콜백은 따로 센다
        assert client.get("/api/auth/oauth/google/callback", params={"error": "access_denied"}, follow_redirects=False).status_code == 302

    def test_callback_per_ip_per_minute(self, client, on, monkeypatch):
        configure(monkeypatch)
        for _ in range(20):
            client.get("/api/auth/oauth/google/callback", params={"error": "access_denied"}, follow_redirects=False)
        assert client.get("/api/auth/oauth/google/callback", params={"error": "access_denied"}, follow_redirects=False).status_code == 429

    def test_exchange_per_ip_per_minute(self, client, on, fake_db):
        for _ in range(10):
            assert client.post("/api/auth/oauth/exchange", json={"code": "nope-dummy"}).status_code == 400
        r = client.post("/api/auth/oauth/exchange", json={"code": "good-dummy-code"})
        assert r.status_code == 429 and "retry-after" in r.headers

    def test_defaults(self):
        assert rate_limit.DEFAULTS["RATE_LIMIT_OAUTH_PER_MIN"] == 20 and rate_limit.DEFAULTS["RATE_LIMIT_OAUTH_EXCHANGE_PER_MIN"] == 10
