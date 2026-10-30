"""소셜 로그인 순수 로직·공급자 어댑터 단위 테스트(DB 없음, 공급자 HTTP는 가짜): state·쿠키 서명, PKCE, 계정 결정 규칙,
설정, 인증 URL, 토큰 교환·프로필 파싱, ID 토큰 클레임 검증, 비밀값이 URL·예외에 없는지, 비밀번호 없는 계정 처리."""

import base64
import hashlib
import logging
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit

import pytest
import requests

from app import http_retry, oauth, oauth_providers
from app.auth import PasswordNotSet, check_current_password, normalize_username, verify_password
from oauth_fakes import (GOOGLE_ID_DUMMY, GOOGLE_SECRET_DUMMY, NAVER_ID_DUMMY, NAVER_SECRET_DUMMY, FakeProvider, FakeResponse,
                         configure, google_claims, state_payload)

SECRET_DUMMY = "dummy-signing-secret-" + "x" * 40
NOW = datetime(2026, 10, 30, 12, 0, tzinfo=timezone.utc)


class TestStateAndCookie:
    def test_roundtrip_and_contents(self):
        s = oauth.start_login("google", SECRET_DUMMY, NOW)
        got = oauth.verify_callback("google", s.state, s.cookie, SECRET_DUMMY, NOW + timedelta(minutes=9))
        assert got.nonce == s.nonce and got.code_verifier == s.code_verifier
        payload = state_payload(s.state)
        assert set(payload) == {"t", "p", "n", "e"} and payload["t"] == "state"
        assert s.code_verifier not in s.state  # verifier는 state(공급자 URL)에 없다
        assert payload["e"] == int((NOW + timedelta(minutes=10)).timestamp())

    def test_naver_has_no_pkce(self):
        s = oauth.start_login("naver", SECRET_DUMMY, NOW)
        assert s.code_verifier is None and s.code_challenge is None
        assert oauth.verify_callback("naver", s.state, s.cookie, SECRET_DUMMY, NOW).code_verifier is None

    @pytest.mark.parametrize("mutate", [
        lambda st: st[:-2] + ("AA" if not st.endswith("AA") else "BB"),  # 서명 변조
        lambda st: oauth._b64(b'{"e":9999999999,"n":"x","p":"google","t":"state"}') + "." + st.split(".")[1],  # 본문 바꿔치기
        lambda st: st.replace(".", ""), lambda st: "", lambda st: st + ".x", lambda st: "a" * 5000,
    ])
    def test_tampered_state_is_rejected(self, mutate):
        s = oauth.start_login("google", SECRET_DUMMY, NOW)
        with pytest.raises(oauth.InvalidState):
            oauth.verify_callback("google", mutate(s.state), s.cookie, SECRET_DUMMY, NOW)

    def test_other_secret_is_rejected(self):
        s = oauth.start_login("google", SECRET_DUMMY, NOW)
        with pytest.raises(oauth.InvalidState):
            oauth.verify_callback("google", s.state, s.cookie, SECRET_DUMMY + "y", NOW)

    def test_expired(self):
        s = oauth.start_login("google", SECRET_DUMMY, NOW)
        with pytest.raises(oauth.InvalidState):
            oauth.verify_callback("google", s.state, s.cookie, SECRET_DUMMY, NOW + timedelta(minutes=10))

    def test_nonce_mismatch_between_state_and_cookie(self):
        a, b = oauth.start_login("google", SECRET_DUMMY, NOW), oauth.start_login("google", SECRET_DUMMY, NOW)
        with pytest.raises(oauth.InvalidState):
            oauth.verify_callback("google", a.state, b.cookie, SECRET_DUMMY, NOW)  # 공격자의 state + 피해자 쿠키

    def test_missing_cookie(self):
        s = oauth.start_login("naver", SECRET_DUMMY, NOW)
        for cookie in (None, ""):
            with pytest.raises(oauth.InvalidState):
                oauth.verify_callback("naver", s.state, cookie, SECRET_DUMMY, NOW)

    def test_cookie_and_state_are_not_interchangeable(self):
        s = oauth.start_login("naver", SECRET_DUMMY, NOW)
        with pytest.raises(oauth.InvalidState):
            oauth.verify_callback("naver", s.cookie, s.cookie, SECRET_DUMMY, NOW)
        with pytest.raises(oauth.InvalidState):
            oauth.verify_callback("naver", s.state, s.state, SECRET_DUMMY, NOW)

    def test_provider_mismatch(self):
        s = oauth.start_login("naver", SECRET_DUMMY, NOW)
        with pytest.raises(oauth.InvalidState):
            oauth.verify_callback("google", s.state, s.cookie, SECRET_DUMMY, NOW)

    def test_google_cookie_without_verifier_is_rejected(self):
        s = oauth.start_login("google", SECRET_DUMMY, NOW)
        exp = int((NOW + timedelta(minutes=5)).timestamp())
        forged = oauth.sign({"t": "cookie", "p": "google", "n": s.nonce, "e": exp}, SECRET_DUMMY)
        with pytest.raises(oauth.InvalidState):
            oauth.verify_callback("google", s.state, forged, SECRET_DUMMY, NOW)


class TestPkce:
    def test_rfc7636_vector(self):
        assert oauth.pkce_challenge("dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk") == "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"

    def test_generated_verifier_shape_and_challenge(self):
        s = oauth.start_login("google", SECRET_DUMMY, NOW)
        assert 43 <= len(s.code_verifier) <= 128 and oauth._VERIFIER_RE.fullmatch(s.code_verifier)
        want = base64.urlsafe_b64encode(hashlib.sha256(s.code_verifier.encode()).digest()).rstrip(b"=").decode()
        assert s.code_challenge == want and "=" not in s.code_challenge

    @pytest.mark.parametrize("bad", ["short", "a" * 129, "a" * 42 + "!", ""])
    def test_bad_verifier(self, bad):
        with pytest.raises(ValueError):
            oauth.pkce_challenge(bad)


def P(email="a@b.com", verified=True):
    return oauth.Profile("google", "sub-1", email, verified)


class TestDecideAccount:
    @pytest.mark.parametrize("profile, identity, local, want", [
        (P(), 7, None, oauth.Decision("login", 7)),  # 이미 연결된 identity
        (P(), 7, oauth.LocalAccount(9, True), oauth.Decision("login", 7)),  # identity가 이메일보다 우선
        (P(verified=True), None, oauth.LocalAccount(9, True), oauth.Decision("link", 9)),  # 양쪽 검증된 같은 이메일
        (P(verified=True), None, oauth.LocalAccount(9, False), oauth.Decision("create")),  # 로컬 미인증 → 연결 안 함, 이메일 없이
        (P(verified=False), None, oauth.LocalAccount(9, True), oauth.Decision("create")),  # 공급자 미검증 → 연결 안 함
        (P(verified=False), None, oauth.LocalAccount(9, False), oauth.Decision("create")),
        (P(verified=True), None, None, oauth.Decision("create", email="a@b.com", email_verified=True)),  # 충돌 없음
        (P(verified=False), None, None, oauth.Decision("create")),  # 미검증 이메일은 저장하지 않는다
        (P(email=None, verified=False), None, None, oauth.Decision("create")),
    ])
    def test_rules(self, profile, identity, local, want):
        assert oauth.decide_account(profile, identity, local) == want

    def test_username_rule(self):
        names = {oauth.new_username("google") for _ in range(200)}
        assert len(names) == 200
        for n in list(names)[:20] + [oauth.new_username("naver")]:
            prefix, rand = n.split("_")
            assert normalize_username(n) == n and prefix in ("google", "naver") and len(rand) == 8 and len(n) <= 20


class TestConfig:
    def test_both_values_required(self, monkeypatch):
        assert oauth.configured_providers() == []
        monkeypatch.setenv("GOOGLE_CLIENT_ID", GOOGLE_ID_DUMMY)
        assert oauth.provider_config("google") is None
        monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "  ")
        assert oauth.provider_config("google") is None
        monkeypatch.setenv("GOOGLE_CLIENT_SECRET", GOOGLE_SECRET_DUMMY)
        assert oauth.configured_providers() == ["google"]

    def test_news_api_keys_do_not_enable_naver_login(self, monkeypatch):
        monkeypatch.setenv("NAVER_CLIENT_ID", "dummy-news-id")
        monkeypatch.setenv("NAVER_CLIENT_SECRET", "dummy-news-secret")
        assert oauth.provider_config("naver") is None
        configure(monkeypatch, google=False)
        assert oauth.configured_providers() == ["naver"]

    def test_repr_hides_secret(self, monkeypatch):
        configure(monkeypatch)
        cfg = oauth.provider_config("google")
        assert GOOGLE_SECRET_DUMMY not in repr(cfg) and GOOGLE_SECRET_DUMMY not in str(cfg) and "secret_set=True" in repr(cfg)

    def test_urls(self, monkeypatch):
        assert oauth.callback_url("google") == "http://localhost:8000/api/auth/oauth/google/callback" and not oauth.cookie_secure()
        monkeypatch.setenv("OAUTH_REDIRECT_BASE", "https://api.example.com/ ")
        assert oauth.callback_url("naver") == "https://api.example.com/api/auth/oauth/naver/callback" and oauth.cookie_secure()


class TestAuthorizeUrl:
    def test_google(self, monkeypatch):
        configure(monkeypatch)
        s = oauth.start_login("google", SECRET_DUMMY, NOW)
        u = urlsplit(oauth_providers.authorize_url(oauth.provider_config("google"), s.state, s.nonce, s.code_challenge))
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        assert f"{u.scheme}://{u.netloc}{u.path}" == oauth_providers.GOOGLE_AUTH_URL
        assert q == {"client_id": GOOGLE_ID_DUMMY, "redirect_uri": "http://localhost:8000/api/auth/oauth/google/callback",
                     "response_type": "code", "scope": "openid email profile", "state": s.state, "nonce": s.nonce,
                     "code_challenge": s.code_challenge, "code_challenge_method": "S256", "prompt": "select_account"}
        assert GOOGLE_SECRET_DUMMY not in u.geturl() and s.code_verifier not in u.geturl()

    def test_naver(self, monkeypatch):
        configure(monkeypatch)
        s = oauth.start_login("naver", SECRET_DUMMY, NOW)
        url = oauth_providers.authorize_url(oauth.provider_config("naver"), s.state, s.nonce, None)
        q = {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}
        assert url.startswith(oauth_providers.NAVER_AUTH_URL + "?") and NAVER_SECRET_DUMMY not in url
        assert q == {"response_type": "code", "client_id": NAVER_ID_DUMMY, "state": s.state,
                     "redirect_uri": "http://localhost:8000/api/auth/oauth/naver/callback"}


@pytest.fixture
def fake(monkeypatch):
    f = FakeProvider()
    monkeypatch.setattr(oauth_providers, "HTTP", f)
    configure(monkeypatch)
    return f


def gcfg():
    return oauth.provider_config("google")


class TestGoogleAdapter:
    def test_success_and_request_shape(self, fake):
        fake.google_claims = google_claims(nonce="n1", email="User@Gmail.com")
        p = oauth_providers.fetch_profile(gcfg(), "code-1", "st", "n1", "v" * 43, datetime.now(timezone.utc))
        assert p == oauth.Profile("google", "g-123", "user@gmail.com", True, "Gil Dong")
        method, url, data = fake.calls[0]
        assert method == "POST" and url == oauth_providers.GOOGLE_EXCHANGE_URL and "?" not in url
        assert data == {"code": "code-1", "client_id": GOOGLE_ID_DUMMY, "client_secret": GOOGLE_SECRET_DUMMY,
                        "redirect_uri": "http://localhost:8000/api/auth/oauth/google/callback",
                        "grant_type": "authorization_code", "code_verifier": "v" * 43}

    @pytest.mark.parametrize("override", [
        {"aud": "someone-else"}, {"iss": "https://evil.example"}, {"exp_in": -1}, {"nonce": "other"}, {"sub": ""},
    ])
    def test_claims_are_checked(self, fake, override):
        kw = {"nonce": "n1", **override}
        fake.google_claims = google_claims(**kw)
        with pytest.raises(oauth_providers.OAuthProviderError) as e:
            oauth_providers.fetch_profile(gcfg(), "c", "s", "n1", "v" * 43, datetime.now(timezone.utc))
        assert e.value.args == ("id_token_invalid",)

    def test_aud_list_and_unverified_email(self, fake):
        fake.google_claims = google_claims(nonce="n1", aud=[GOOGLE_ID_DUMMY, "x"], email_verified=False)
        p = oauth_providers.fetch_profile(gcfg(), "c", "s", "n1", "v" * 43, datetime.now(timezone.utc))
        assert p.email == "user@gmail.com" and p.email_verified is False
        fake.google_claims = google_claims(nonce="n1", email="not-an-email")
        assert oauth_providers.fetch_profile(gcfg(), "c", "s", "n1", "v" * 43, datetime.now(timezone.utc)).email is None

    @pytest.mark.parametrize("err, reason", [
        (requests.Timeout("timed out with client_secret=" + GOOGLE_SECRET_DUMMY), "token_http"),
        (requests.ConnectionError("boom"), "token_http"),
        (FakeResponse(200, None, text="<html>"), "token_error"),
        (FakeResponse(200, {"error": "invalid_grant"}), "id_token_invalid"),
    ])
    def test_failures_are_coded_without_secrets(self, fake, err, reason):
        fake.google_token_error = err
        with pytest.raises(oauth_providers.OAuthProviderError) as e:
            oauth_providers.fetch_profile(gcfg(), "c", "s", "n1", "v" * 43, datetime.now(timezone.utc))
        assert e.value.args == (reason,) and GOOGLE_SECRET_DUMMY not in str(e.value) and e.value.__cause__ is None

    @pytest.mark.parametrize("token", [None, "", "a.b", "a.!!!.c", "a." + base64.urlsafe_b64encode(b"[1]").decode() + ".c"])
    def test_malformed_id_token(self, token):
        with pytest.raises(oauth_providers.OAuthProviderError):
            oauth_providers.id_token_claims(token)


class TestNaverAdapter:
    def ncfg(self):
        return oauth.provider_config("naver")

    def test_success_token_in_body_not_url(self, fake):
        p = oauth_providers.fetch_profile(self.ncfg(), "code-n", "state-n", "nonce", None, NOW)
        assert p == oauth.Profile("naver", "n-123", "user@naver.com", True, "홍길동")
        (m1, u1, d1), (m2, u2, h2) = fake.calls
        assert (m1, u1) == ("POST", oauth_providers.NAVER_EXCHANGE_URL) and d1["client_secret"] == NAVER_SECRET_DUMMY and "?" not in u1
        assert (m2, u2) == ("GET", oauth_providers.NAVER_PROFILE_URL) and h2 == {"Authorization": "Bearer dummy-naver-access"}

    def test_no_email_consent(self, fake):
        fake.naver_profile = {"resultcode": "00", "response": {"id": "n-9"}}
        p = oauth_providers.fetch_profile(self.ncfg(), "c", "s", "n", None, NOW)
        assert p.email is None and p.email_verified is False

    @pytest.mark.parametrize("setup, reason", [
        (lambda f: setattr(f, "naver_token", {"error": "invalid_request", "error_description": "no"}), "token_error"),
        (lambda f: setattr(f, "naver_token_error", requests.Timeout("t")), "token_http"),
        (lambda f: setattr(f, "naver_profile", {"resultcode": "024", "message": "Authentication failed"}), "profile_invalid"),
        (lambda f: setattr(f, "naver_profile", {"resultcode": "00", "response": {"id": ""}}), "profile_invalid"),
        (lambda f: setattr(f, "naver_profile_error", requests.ConnectionError("x")), "profile_http"),
    ])
    def test_failures(self, fake, setup, reason):
        setup(fake)
        with pytest.raises(oauth_providers.OAuthProviderError) as e:
            oauth_providers.fetch_profile(self.ncfg(), "c", "s", "n", None, NOW)
        assert e.value.args == (reason,) and NAVER_SECRET_DUMMY not in str(e.value)


class TestHttpRetryPost:
    class Session:
        def __init__(self, statuses):
            self.statuses, self.calls = list(statuses), 0

        def post(self, url, data, headers, timeout):
            self.calls += 1
            return FakeResponse(self.statuses.pop(0), {"ok": True})

    def test_retries_transient_then_succeeds(self):
        s = self.Session([503, 200])
        r = http_retry.post("https://x.example/t", data={"a": 1}, session=s, sleep=lambda _: None)
        assert r.status_code == 200 and s.calls == 2

    def test_4xx_is_not_retried(self):
        s = self.Session([400, 200])
        with pytest.raises(requests.HTTPError):
            http_retry.post("https://x.example/t", data={}, session=s, sleep=lambda _: None)
        assert s.calls == 1

    def test_retry_log_has_no_body(self, caplog):
        s = self.Session([500, 200])
        with caplog.at_level(logging.WARNING):
            http_retry.post("https://x.example/t", data={"client_secret": "dummy-in-body"}, session=s, sleep=lambda _: None)
        assert "dummy-in-body" not in caplog.text


class TestNullPasswordPaths:
    def test_verify_password_with_no_hash_is_false_and_safe(self):
        assert verify_password("anything", None) is False and verify_password("anything", "") is False
        assert verify_password("x", "!no-password") is False  # 다운그레이드가 넣는 표식 값

    def test_no_hash_still_runs_one_argon2_verification(self, monkeypatch):
        """비밀번호 없는 계정도 실제 해시 검증 1회만큼 시간을 쓴다(응답 시간으로 소셜 계정이 드러나지 않게)."""
        from app import auth

        dummy = auth._dummy_hash()  # 캐시를 먼저 채운다
        calls = []

        class Spy:
            def __init__(self, real):
                self.real = real

            def verify(self, h, p):
                calls.append(h)
                return self.real.verify(h, p)

        monkeypatch.setattr(auth, "_hasher", Spy(auth._hasher))
        assert verify_password("anything", None) is False
        assert calls == [dummy]

    def test_check_current_password_raises_for_no_hash(self):
        for missing in (None, ""):
            with pytest.raises(PasswordNotSet):
                check_current_password(missing, "anything")
