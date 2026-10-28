"""CORS 설정, 약한 JWT 시크릿 기동 검사, 전역 500 핸들러, DB 오류 로그 마스킹(app/config.py, app/main.py). DB·Ollama 없음."""

import logging

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

import app.api.status as status_api
from app import config
from app.main import SERVER_ERROR_DETAIL, app

STRONG = "Qm7vR2xL9pT4kW8nZ3bF6hJ1cY5gD0sA-uE_iO2rN7tV"


class TestCorsOrigins:
    def test_default_is_dev_5173_only(self, monkeypatch):
        monkeypatch.delenv("CORS_ALLOW_ORIGINS", raising=False)
        assert config.cors_origins() == ["http://localhost:5173", "http://127.0.0.1:5173"]

    def test_env_list(self, monkeypatch):
        monkeypatch.setenv("CORS_ALLOW_ORIGINS", " https://demo.example , http://localhost:5173/ ,")
        assert config.cors_origins() == ["https://demo.example", "http://localhost:5173"]

    def test_wildcard(self, monkeypatch):
        monkeypatch.setenv("CORS_ALLOW_ORIGINS", "*")
        assert config.cors_origins() == ["*"]


class TestCorsMiddleware:
    """앱은 import 시점의 설정(테스트 환경: 기본값)으로 만들어진다."""

    client = TestClient(app, raise_server_exceptions=False)

    def test_allowed_origin_gets_cors_header_without_credentials(self):
        r = self.client.get("/api/disclaimer", headers={"Origin": "http://localhost:5173"})
        assert r.status_code == 200 and r.headers["access-control-allow-origin"] == "http://localhost:5173"
        assert "access-control-allow-credentials" not in r.headers

    @pytest.mark.parametrize("origin", ["http://evil.example", "http://localhost:3000", "http://localhost:5174"])
    def test_other_origin_gets_no_cors_header(self, origin):
        r = self.client.get("/api/disclaimer", headers={"Origin": origin})
        assert r.status_code == 200 and "access-control-allow-origin" not in r.headers

    def test_preflight_allowed(self):
        r = self.client.options("/api/research", headers={
            "Origin": "http://127.0.0.1:5173", "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type"})
        assert r.status_code == 200
        assert r.headers["access-control-allow-origin"] == "http://127.0.0.1:5173"
        assert "authorization" in r.headers["access-control-allow-headers"].lower()

    def test_preflight_rejected_for_other_origin(self):
        r = self.client.options("/api/research", headers={
            "Origin": "http://evil.example", "Access-Control-Request-Method": "POST"})
        assert r.status_code == 400 and "access-control-allow-origin" not in r.headers


class TestJwtSecret:
    @pytest.mark.parametrize("secret", ["change-me-change-me-change-me-change-me", "a" * 40,
                                        "my-jwt-secret-key-for-development-12345", "<32자 이상 임의 문자열로 교체해 주세요 아무 값이나>"])
    def test_weak_values(self, secret):
        assert config.jwt_secret_problems(secret)

    def test_strong_value(self):
        assert config.jwt_secret_problems(STRONG) == []

    def test_production_refuses_weak_secret_at_startup(self, monkeypatch):
        monkeypatch.setenv("APP_ENV", "production")
        monkeypatch.setenv("JWT_SECRET", "change-me-change-me-change-me-change-me")
        with pytest.raises(RuntimeError, match="JWT_SECRET이 약합니다"):
            with TestClient(app):
                pass

    def test_development_only_warns(self, monkeypatch, caplog):
        monkeypatch.setenv("APP_ENV", "development")
        weak = "change-me-change-me-change-me-change-me"
        monkeypatch.setenv("JWT_SECRET", weak)
        with caplog.at_level(logging.WARNING, logger="app.config"):
            with TestClient(app) as c:
                assert c.get("/api/disclaimer").status_code == 200
        assert "JWT_SECRET이 약합니다" in caplog.text and weak not in caplog.text  # 값은 로그에 남기지 않는다

    def test_production_with_strong_secret_starts(self, monkeypatch):
        monkeypatch.setenv("APP_ENV", "production")
        monkeypatch.setenv("JWT_SECRET", STRONG)
        with TestClient(app) as c:
            assert c.get("/api/disclaimer").status_code == 200

    def test_missing_secret_still_fails_in_every_mode(self, monkeypatch):
        monkeypatch.delenv("JWT_SECRET")
        with pytest.raises(RuntimeError, match="JWT_SECRET"):
            with TestClient(app):
                pass


class TestErrors:
    def test_unhandled_exception_is_a_plain_500(self, monkeypatch, caplog):
        def boom(db):
            raise RuntimeError(r"C:\project\lab-scale\backend\app\x.py SELECT password_hash FROM users")

        monkeypatch.setattr(status_api, "build_status", boom)
        with caplog.at_level(logging.ERROR, logger="app.main"):
            r = TestClient(app, raise_server_exceptions=False).get("/api/status")
        assert r.status_code == 500 and r.json() == {"detail": SERVER_ERROR_DETAIL}
        assert "password_hash" not in r.text and "Traceback" not in r.text
        assert "RuntimeError" in caplog.text  # 상세는 서버 로그에만

    def test_db_error_log_has_no_parameters(self, monkeypatch, caplog):
        def boom(db):
            raise OperationalError("SELECT * FROM users WHERE username = %(u)s", {"u": "alice-pw-Secret123"}, Exception("x"))

        monkeypatch.setattr(status_api, "build_status", boom)
        with caplog.at_level(logging.ERROR, logger="app.main"):
            r = TestClient(app, raise_server_exceptions=False).get("/api/status")
        assert r.status_code == 503
        assert "alice-pw-Secret123" not in caplog.text and "OperationalError" in caplog.text

    def test_validation_error_format_is_unchanged(self):
        r = TestClient(app, raise_server_exceptions=False).get("/api/companies", params={"limit": 0})
        assert r.status_code == 422 and isinstance(r.json()["detail"], list)
