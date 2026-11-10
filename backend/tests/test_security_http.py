"""보안 회귀 테스트(DB 없음): 라우트 전수 인증 순회, 위조 JWT 거부, 보안 헤더·no-store·HSTS, 413, 공개 GET 레이트 리밋(켠 상태),
운영 모드 API 문서 끄기, 접근 로그의 쿼리 제거(uvicorn 포함)."""

import base64
import json
import logging
import re
from datetime import datetime, timedelta, timezone

import jwt
import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

import app.api.deps as deps
from app import rate_limit, security
from app.auth import ACCESS_TOKEN_TTL, CurrentUser, create_access_token
from app.main import app
from test_auth_api import _requires_login

client = TestClient(app, raise_server_exceptions=False)
USER = CurrentUser(id=3, username="alice")
UNAUTHORIZED = {"detail": "인증이 필요합니다. 다시 로그인해주세요."}


def _api_routes():
    for route in app.routes:
        routes = [route] if isinstance(route, APIRoute) else (
            [r for r in route.original_router.routes if isinstance(r, APIRoute)] if hasattr(route, "original_router") else [])
        for r in routes:
            if r.path.startswith("/api"):
                for m in sorted(r.methods):
                    yield m, r.path, _requires_login(r.dependant)


def _concrete(path: str) -> str:
    return re.sub(r"\{[^}]+\}", lambda m: {"{provider}": "google", "{ticker}": "005930"}.get(m.group(0), "1"), path)


PROTECTED = sorted({(m, p) for m, p, needs in _api_routes() if needs})


def _b64(obj) -> str:
    return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()


@pytest.fixture
def known_user(monkeypatch):
    monkeypatch.setattr(deps, "load_user", lambda uid: USER if uid == USER.id else None)


def forged_tokens(secret):
    now = datetime.now(timezone.utc)
    base = {"sub": "3", "iat": now, "exp": now + ACCESS_TOKEN_TTL, "tv": 0}
    ts = int(now.timestamp())
    plain = {"sub": "3", "iat": ts, "exp": ts + 3600, "tv": 0}
    return {
        "alg_none": f"{_b64({'alg': 'none', 'typ': 'JWT'})}.{_b64(plain)}.",
        "alg_none_with_sig": f"{_b64({'alg': 'none', 'typ': 'JWT'})}.{_b64(plain)}.c2ln",
        "hs512_same_key": jwt.encode(base, secret, algorithm="HS512"),
        "wrong_key": jwt.encode(base, secret + "x", algorithm="HS256"),
        "no_exp": jwt.encode({k: v for k, v in base.items() if k != "exp"}, secret, algorithm="HS256"),
        "no_sub": jwt.encode({k: v for k, v in base.items() if k != "sub"}, secret, algorithm="HS256"),
        "no_iat": jwt.encode({k: v for k, v in base.items() if k != "iat"}, secret, algorithm="HS256"),
        "no_tv": jwt.encode({k: v for k, v in base.items() if k != "tv"}, secret, algorithm="HS256"),
        "expired": jwt.encode({**base, "iat": now - timedelta(days=2), "exp": now - timedelta(seconds=1)}, secret, algorithm="HS256"),
        "garbage": "not.a.jwt",
    }


class TestRouteSweep:
    def test_there_are_protected_routes(self):
        assert len(PROTECTED) >= 25

    @pytest.mark.parametrize("method, path", PROTECTED)
    def test_every_protected_route_needs_a_token(self, method, path):
        r = client.request(method, _concrete(path), json={})
        assert r.status_code == 401 and r.json() == UNAUTHORIZED, (method, path)

    @pytest.mark.parametrize("kind", ["alg_none", "alg_none_with_sig", "hs512_same_key", "wrong_key", "no_exp", "no_sub", "no_iat",
                                      "no_tv", "expired", "garbage"])
    def test_forged_tokens_are_401_on_every_protected_route(self, known_user, jwt_secret, kind):
        token = forged_tokens(jwt_secret)[kind]
        for method, path in PROTECTED:
            r = client.request(method, _concrete(path), json={}, headers={"Authorization": f"Bearer {token}"})
            assert r.status_code == 401, (kind, method, path)

    def test_a_valid_token_passes_the_auth_layer(self, known_user, monkeypatch):
        import app.api.auth as auth_api
        monkeypatch.setattr(auth_api.recovery_db, "account_email", lambda db, uid: (None, False))
        r = client.get("/api/auth/me", headers={"Authorization": f"Bearer {create_access_token(3)}"})
        assert r.status_code == 200

    def test_no_admin_or_internal_routes(self):
        paths = {p for _, p, _ in _api_routes()}
        assert not any(re.search(r"admin|internal|debug|metrics|/_", p) for p in paths), paths


class TestHeaders:
    def test_public_response_has_security_headers_but_no_no_store(self):
        r = client.get("/api/disclaimer")
        assert r.headers["x-content-type-options"] == "nosniff" and r.headers["x-frame-options"] == "DENY"
        assert r.headers["referrer-policy"] == "no-referrer" and "camera=()" in r.headers["permissions-policy"]
        assert "no-store" not in r.headers.get("cache-control", "") and "strict-transport-security" not in r.headers

    def test_auth_and_personal_responses_are_no_store(self, known_user):
        assert client.post("/api/auth/login", json={"username": "x", "password": "y"}).headers["cache-control"] == "no-store"
        r = client.get("/api/portfolio", headers={"Authorization": "Bearer nope"})
        assert r.status_code == 401 and r.headers["cache-control"] == "no-store" and r.headers["x-frame-options"] == "DENY"

    def test_error_responses_also_get_headers(self):
        r = client.get("/api/does-not-exist")
        assert r.status_code == 404 and r.headers["x-content-type-options"] == "nosniff"

    def test_existing_cache_control_is_not_overwritten(self, monkeypatch):
        from oauth_fakes import configure
        configure(monkeypatch)
        r = client.get("/api/auth/oauth/google/login", follow_redirects=False)
        assert r.status_code == 302 and r.headers["cache-control"] == "no-store" and r.headers["referrer-policy"] == "no-referrer"

    def test_hsts_only_when_enabled(self, monkeypatch):
        assert "strict-transport-security" not in client.get("/api/disclaimer").headers
        monkeypatch.setenv("SECURITY_HSTS", "1")
        assert client.get("/api/disclaimer").headers["strict-transport-security"] == security.HSTS_VALUE

    def test_cors_still_works(self):
        r = client.options("/api/companies", headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "GET"})
        assert r.headers["access-control-allow-origin"] == "http://localhost:5173"
        r = client.get("/api/disclaimer", headers={"Origin": "http://localhost:5173"})
        assert r.headers["access-control-allow-origin"] == "http://localhost:5173" and r.headers["x-frame-options"] == "DENY"


class TestBodyLimit:
    def test_large_content_length_is_413(self, monkeypatch):
        monkeypatch.setenv("MAX_REQUEST_BODY_BYTES", "2048")
        r = client.post("/api/auth/login", content=b'{"username":"' + b"a" * 3000 + b'","password":"x"}',
                        headers={"Content-Type": "application/json"})
        assert r.status_code == 413 and r.json() == {"detail": security.BODY_TOO_LARGE}
        assert r.headers["x-content-type-options"] == "nosniff"

    def test_chunked_body_is_counted(self, monkeypatch):
        monkeypatch.setenv("MAX_REQUEST_BODY_BYTES", "2048")

        def gen():
            for _ in range(10):
                yield b"a" * 500

        r = client.post("/api/auth/login", content=gen(), headers={"Content-Type": "application/json"})
        assert r.status_code == 413

    def test_default_limit_allows_normal_requests(self):
        assert security.max_body_bytes() == 1_048_576
        assert client.post("/api/auth/login", json={"username": "x", "password": "y" * 100}).status_code in (401, 503)


class TestPublicRateLimit:
    def test_limit_counts_every_get_even_with_fake_auth_header(self, monkeypatch):
        monkeypatch.setenv("RATE_LIMIT_ENABLED", "1")
        monkeypatch.setenv("RATE_LIMIT_PUBLIC_GET_PER_MIN", "3")
        rate_limit.reset_all()
        for i in range(3):
            assert client.get("/api/disclaimer", headers={"Authorization": "Bearer fake"} if i else {}).status_code == 200
        r = client.get("/api/disclaimer")
        assert r.status_code == 429 and int(r.headers["retry-after"]) >= 1 and "요청이 너무 많습니다" in r.json()["detail"]
        assert r.headers["x-content-type-options"] == "nosniff"
        assert client.post("/api/auth/login", json={"username": "x", "password": "y"}).status_code != 429  # GET만 센다

    def test_default_is_generous_and_off_in_tests(self):
        assert rate_limit.DEFAULTS["RATE_LIMIT_PUBLIC_GET_PER_MIN"] == 600
        for _ in range(50):
            assert client.get("/api/disclaimer").status_code == 200


class TestDocsAndInfo:
    def test_docs_follow_app_env(self, monkeypatch):
        assert security.docs_enabled() is True
        monkeypatch.setenv("APP_ENV", "production")
        assert security.docs_enabled() is False

    def test_unhandled_error_leaks_nothing_to_response_or_log(self, monkeypatch, caplog):
        import app.api.disclaimer as d

        # 값은 실행 중에 만든다(스택에는 코드 줄만 찍히므로 소스에 그대로 쓰면 그 줄이 보인다)
        leaked = "INSERT INTO users VALUES ('" + "victim" + "@example.com', '" + "dummy-hash" + "-value')"

        class Boom:
            def __init__(self, *a, **k):
                raise RuntimeError(leaked)

        monkeypatch.setattr(d, "DisclaimerResponse", Boom)
        with caplog.at_level(logging.ERROR):
            r = client.get("/api/disclaimer")
        assert r.status_code == 500 and r.json() == {"detail": "서버 오류가 발생했습니다."}
        assert "victim@example.com" not in caplog.text and "dummy-hash-value" not in caplog.text
        assert "RuntimeError" in caplog.text and "/api/disclaimer" in caplog.text  # 종류·경로·코드 위치만

    def test_server_header_is_not_set_by_the_app(self):
        assert "x-powered-by" not in client.get("/api/disclaimer").headers


class TestAccessLog:
    def test_strip_query(self):
        assert security.strip_query("/api/auth/oauth/google/callback?code=abc&state=def") == "/api/auth/oauth/google/callback"
        assert security.strip_query("/api/companies") == "/api/companies"

    def test_uvicorn_access_records_lose_the_query(self):
        assert any(isinstance(f, security.QueryRedactFilter) for f in logging.getLogger("uvicorn.access").filters)
        record = logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1, '%s - "%s %s HTTP/%s" %d',
                                   ("127.0.0.1:5000", "GET", "/api/auth/oauth/naver/callback?code=dummy-code-123&state=dummy-state-456", "1.1", 302), None)
        for f in logging.getLogger("uvicorn.access").filters:
            f.filter(record)
        msg = record.getMessage()
        assert "dummy-code-123" not in msg and "dummy-state-456" not in msg and "/api/auth/oauth/naver/callback" in msg

    def test_app_access_log_never_has_query(self, caplog):
        with caplog.at_level(logging.INFO, logger="app.access"):
            client.get("/api/auth/oauth/google/callback", params={"code": "dummy-code-xyz", "state": "dummy-state-xyz"}, follow_redirects=False)
        text = "\n".join(r.getMessage() for r in caplog.records if r.name == "app.access")
        assert "/api/auth/oauth/google/callback" in text and "dummy-code-xyz" not in text and "dummy-state-xyz" not in text
