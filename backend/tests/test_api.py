import ollama
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

import app.api.research as research_api
from app.api.research import MAX_QUESTION_LEN
from app.main import app

client = TestClient(app, raise_server_exceptions=False)

OK_RESULT = {"answer": "답변", "used_tools": ["stock_tool"], "sources": [], "report_id": None}


@pytest.fixture
def calls(monkeypatch):
    """ask_question을 가짜로 바꿔 LLM/DB 없이 라우트만 검증한다. 호출된 질문을 기록한다."""
    seen = []

    def fake(question):
        seen.append(question)
        return OK_RESULT

    monkeypatch.setattr(research_api, "ask_question", fake)
    return seen


def post(question):
    return client.post("/api/research", json={"question": question})


class TestQuestionValidation:
    @pytest.mark.parametrize("question", ["", "   ", "\n\t "])
    def test_blank_question_is_422(self, calls, question):
        r = post(question)
        assert r.status_code == 422
        assert r.json()["detail"][0]["loc"] == ["body", "question"]
        assert calls == []

    def test_too_long_question_is_422(self, calls):
        r = post("가" * (MAX_QUESTION_LEN + 1))
        assert r.status_code == 422
        assert r.json()["detail"][0]["type"] == "string_too_long"
        assert calls == []

    def test_max_length_question_accepted(self, calls):
        assert post("가" * MAX_QUESTION_LEN).status_code == 200

    def test_missing_field_is_422(self, calls):
        assert client.post("/api/research", json={}).status_code == 422

    def test_question_is_stripped_before_agent(self, calls):
        r = post("  삼성전자 주가  ")
        assert r.status_code == 200
        assert r.json() == OK_RESULT
        assert calls == ["삼성전자 주가"]


class TestDependencyErrors:
    def _raise(self, monkeypatch, exc):
        def boom(question):
            raise exc

        monkeypatch.setattr(research_api, "ask_question", boom)

    def test_ollama_unreachable_is_503(self, monkeypatch):
        self._raise(monkeypatch, ConnectionError("Failed to connect to Ollama"))
        r = post("삼성전자 주가")
        assert r.status_code == 503
        assert "Ollama" in r.json()["detail"]

    def test_ollama_error_response_is_502(self, monkeypatch):
        self._raise(monkeypatch, ollama.ResponseError("model not found", 404))
        r = post("삼성전자 주가")
        assert r.status_code == 502
        assert isinstance(r.json()["detail"], str)

    def test_db_unreachable_is_503(self, monkeypatch):
        self._raise(monkeypatch, OperationalError("SELECT 1", {}, Exception("connection refused")))
        r = post("삼성전자 주가")
        assert r.status_code == 503
        assert "데이터베이스" in r.json()["detail"]

    def test_db_error_on_list_is_503(self, monkeypatch):
        def boom(limit, offset):
            raise OperationalError("SELECT 1", {}, Exception("connection refused"))

        monkeypatch.setattr(research_api, "list_reports", boom)
        assert client.get("/api/research").status_code == 503


class TestReportRoutes:
    def test_list_envelope_and_defaults(self, monkeypatch):
        seen = {}

        def fake(limit, offset):
            seen.update(limit=limit, offset=offset)
            return 0, []

        monkeypatch.setattr(research_api, "list_reports", fake)
        r = client.get("/api/research")
        assert r.status_code == 200
        assert r.json() == {"total": 0, "items": []}
        assert seen == {"limit": 20, "offset": 0}

    @pytest.mark.parametrize("query", ["limit=0", "limit=101", "offset=-1", "limit=abc"])
    def test_list_param_out_of_range_is_422(self, query):
        assert client.get(f"/api/research?{query}").status_code == 422

    def test_detail_404(self, monkeypatch):
        monkeypatch.setattr(research_api, "get_report", lambda report_id: None)
        r = client.get("/api/research/12345")
        assert r.status_code == 404
        assert "12345" in r.json()["detail"]

    @pytest.mark.parametrize("report_id", ["0", "abc", "99999999999999999999"])
    def test_detail_bad_id_is_422(self, report_id):
        assert client.get(f"/api/research/{report_id}").status_code == 422

    def test_cors_allows_frontend_origin(self, monkeypatch):
        monkeypatch.setattr(research_api, "list_reports", lambda limit, offset: (0, []))
        r = client.get("/api/research", headers={"Origin": "http://localhost:5173"})
        assert r.headers["access-control-allow-origin"] == "http://localhost:5173"
        r = client.get("/api/research", headers={"Origin": "http://evil.example"})
        assert "access-control-allow-origin" not in r.headers
