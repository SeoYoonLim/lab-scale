"""GET /api/health(app/health.py)와 요청 로그 미들웨어(app/main.py). 검사 함수는 가짜로 바꿔 DB·Ollama 없이 돈다."""

import logging
import time
from datetime import date

import pytest
from fastapi.testclient import TestClient

import app.api.status as status_api
import app.main as main
from app import health
from app.main import app

client = TestClient(app, raise_server_exceptions=False)


def ok():
    return {"ok": True}


class TestRunChecks:
    def test_all_ok(self):
        assert health.run_checks({"a": ok, "b": ok}) == {"status": "ok", "checks": {"a": {"ok": True}, "b": {"ok": True}}}

    def test_failure_is_degraded_with_type_only_reason(self):
        def boom():
            raise ConnectionError("connect to 10.0.0.5:5432 failed password=hunter2")

        out = health.run_checks({"a": ok, "db": boom})
        assert out["status"] == "degraded"
        assert out["checks"]["db"] == {"ok": False, "reason": "연결 또는 조회 실패(ConnectionError)"}
        assert "10.0.0.5" not in str(out) and "hunter2" not in str(out)

    def test_hanging_check_does_not_block_the_response(self):
        out_t0 = time.perf_counter()
        out = health.run_checks({"a": ok, "slow": lambda: time.sleep(2) or {"ok": True}}, timeout=0.2)
        assert time.perf_counter() - out_t0 < 1.0
        assert out["status"] == "degraded" and out["checks"]["slow"] == {"ok": False, "reason": "응답 시간 초과"}

    def test_checks_run_in_parallel(self):
        t0 = time.perf_counter()
        health.run_checks({f"c{i}": (lambda: time.sleep(0.3) or {"ok": True}) for i in range(4)}, timeout=2)
        assert time.perf_counter() - t0 < 0.9


class TestOllamaCheck:
    class Resp:
        def __init__(self, data):
            self.data = data

        def json(self):
            return self.data

    def fake_get(self, tags, ps):
        def get(url, timeout=None):
            assert timeout == (1, health.CHECK_TIMEOUT_S)
            return self.Resp({"models": [{"name": n} for n in (tags if url.endswith("/api/tags") else ps)]})
        return get

    def test_installed_and_loaded(self):
        out = health.check_ollama(self.fake_get(["llama3.1:8b", "bge-m3:latest"], ["llama3.1:8b"]))
        assert out == {"ok": True, "model_installed": True, "model_loaded": True}

    def test_installed_not_loaded_is_still_ok(self):
        assert health.check_ollama(self.fake_get(["llama3.1:8b"], []))["ok"] is True

    def test_missing_model(self):
        out = health.check_ollama(self.fake_get(["bge-m3:latest"], []))
        assert out["ok"] is False and out["model_installed"] is False

    def test_default_host_is_ipv4_loopback(self, monkeypatch):
        monkeypatch.delenv("OLLAMA_HOST", raising=False)
        assert health._ollama_host() == "http://127.0.0.1:11434"


class TestEndpoint:
    def test_public_200_ok(self, monkeypatch):
        monkeypatch.setattr(health, "CHECKS", {"db": ok, "migrations": ok, "ollama": ok,
                                               "data": lambda: {"ok": True, "latest_date": "2026-10-27", "stale": False}})
        r = client.get("/api/health")
        assert r.status_code == 200 and r.json()["status"] == "ok"
        assert set(r.json()["checks"]) == {"db", "migrations", "ollama", "data"}

    def test_degraded_is_still_200(self, monkeypatch):
        def down():
            raise OSError("db down")

        monkeypatch.setattr(health, "CHECKS", {"db": down, "ollama": ok})
        r = client.get("/api/health")
        assert r.status_code == 200 and r.json()["status"] == "degraded"
        assert r.json()["checks"]["db"]["ok"] is False

    def test_data_check_uses_the_stale_rule(self, monkeypatch):
        import app.db.session as session

        class Q:
            def scalar(self):
                return date(2026, 10, 14)

        class DB:
            def query(self, *a):
                return Q()

            def close(self):
                pass

        monkeypatch.setattr(session, "SessionLocal", lambda: DB())
        out = health.check_data(today=date(2026, 10, 28))
        assert out == {"ok": False, "latest_date": "2026-10-14", "stale": True, "reason": "시세 데이터가 오래됐습니다"}


class TestAccessLog:
    def test_logs_method_path_status_ms_user_without_query_or_token(self, caplog, monkeypatch):
        from app.api import deps

        monkeypatch.setattr(deps, "decode_token_claims", lambda t: (77, 0))
        monkeypatch.setattr(deps, "load_user", lambda uid: deps.CurrentUser(id=uid, username="u"))
        token = "eyJhbGciOiJIUzI1NiJ9.secret-token-value.sig"
        with caplog.at_level(logging.INFO, logger="app.access"):
            client.get("/api/auth/me", params={"q": "private-query"}, headers={"Authorization": f"Bearer {token}"})
        line = [r.getMessage() for r in caplog.records if r.name == "app.access"][-1]
        assert line.startswith("GET /api/auth/me 200 ") and line.endswith("ms user=77")
        assert "private-query" not in caplog.text and token not in caplog.text and "secret-token" not in caplog.text

    def test_anonymous_and_errors(self, caplog):
        with caplog.at_level(logging.INFO, logger="app.access"):
            client.get("/api/auth/me")
        line = [r.getMessage() for r in caplog.records if r.name == "app.access"][-1]
        assert line.startswith("GET /api/auth/me 401 ") and line.endswith("user=-")

    def test_slow_request_is_warning(self, caplog, monkeypatch):
        monkeypatch.setattr(main, "SLOW_REQUEST_MS", 0)
        with caplog.at_level(logging.INFO, logger="app.access"):
            client.get("/api/disclaimer")
        rec = [r for r in caplog.records if r.name == "app.access"][-1]
        assert rec.levelno == logging.WARNING

    def test_unhandled_error_is_logged_as_500(self, caplog, monkeypatch):
        monkeypatch.setattr(status_api, "build_status", lambda db: 1 / 0)
        with caplog.at_level(logging.INFO, logger="app.access"):
            assert client.get("/api/status").status_code == 500
        assert any(r.getMessage().startswith("GET /api/status 500") for r in caplog.records if r.name == "app.access")


@pytest.mark.integration
def test_real_db_and_migration_checks(dev_db):
    """실제 dev DB(CI는 마이그레이션만 올린 빈 DB): DB 연결과 마이그레이션 head가 맞아야 한다. Ollama·시세는 환경마다 달라 보지 않는다."""
    out = health.run_checks()
    assert out["checks"]["db"] == {"ok": True}
    assert out["checks"]["migrations"] == {"ok": True}
    assert set(out["checks"]) == {"db", "migrations", "ollama", "data"}
    assert "15432" not in str(out) and "postgres" not in str(out)
