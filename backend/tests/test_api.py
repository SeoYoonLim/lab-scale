from datetime import datetime, timezone

import ollama
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

import app.api.research as research_api
from app.api.research import MAX_QUESTION_LEN
from app.disclaimer import DISCLAIMER
from app.main import app

client = TestClient(app, raise_server_exceptions=False)

OK_RESULT = {
    "answer": "답변", "used_tools": ["stock_tool"], "sources": [], "report_id": None, "previous_report_id": None,
}


USER_ID = 1


@pytest.fixture(autouse=True)
def _logged_in(login_as):
    """리서치 라우트는 로그인이 필요하다. 인증 자체는 test_auth_api.py에서 검증하고 여기서는 가짜 사용자로 통과시킨다."""
    login_as(USER_ID)


@pytest.fixture
def calls(monkeypatch):
    """ask_question을 가짜로 바꿔 LLM/DB 없이 라우트만 검증한다. 호출된 질문을 기록한다."""
    seen = []

    def fake(question, user_id=None):
        assert user_id == USER_ID
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
        # 기존 필드는 그대로이고 disclaimer만 추가됐다
        assert r.json() == {**OK_RESULT, "disclaimer": DISCLAIMER}
        assert calls == ["삼성전자 주가"]


class TestDependencyErrors:
    def _raise(self, monkeypatch, exc):
        def boom(question, user_id=None):
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
        def boom(user_id, limit, offset):
            raise OperationalError("SELECT 1", {}, Exception("connection refused"))

        monkeypatch.setattr(research_api, "list_reports", boom)
        assert client.get("/api/research").status_code == 503


class TestReportRoutes:
    def test_list_envelope_and_defaults(self, monkeypatch):
        seen = {}

        def fake(user_id, limit, offset):
            seen.update(user_id=user_id, limit=limit, offset=offset)
            return 0, []

        monkeypatch.setattr(research_api, "list_reports", fake)
        r = client.get("/api/research")
        assert r.status_code == 200
        assert r.json() == {"total": 0, "items": []}
        assert seen == {"user_id": USER_ID, "limit": 20, "offset": 0}

    @pytest.mark.parametrize("query", ["limit=0", "limit=101", "offset=-1", "limit=abc"])
    def test_list_param_out_of_range_is_422(self, query):
        assert client.get(f"/api/research?{query}").status_code == 422

    def test_detail_404(self, monkeypatch):
        monkeypatch.setattr(research_api, "get_report", lambda report_id, user_id: None)
        r = client.get("/api/research/12345")
        assert r.status_code == 404
        assert "12345" in r.json()["detail"]

    @pytest.mark.parametrize("report_id", ["0", "abc", "99999999999999999999"])
    def test_detail_bad_id_is_422(self, report_id):
        assert client.get(f"/api/research/{report_id}").status_code == 422

    def test_cors_allows_frontend_origin(self, monkeypatch):
        monkeypatch.setattr(research_api, "list_reports", lambda user_id, limit, offset: (0, []))
        r = client.get("/api/research", headers={"Origin": "http://localhost:5173"})
        assert r.headers["access-control-allow-origin"] == "http://localhost:5173"
        r = client.get("/api/research", headers={"Origin": "http://evil.example"})
        assert "access-control-allow-origin" not in r.headers


class TestDeleteReport:
    def test_delete_then_get_is_404(self, monkeypatch):
        store = {7: {"report_id": 7}}
        monkeypatch.setattr(
            research_api, "delete_report", lambda report_id, user_id: store.pop(report_id, None) is not None
        )
        monkeypatch.setattr(research_api, "get_report", lambda report_id, user_id: store.get(report_id))

        r = client.delete("/api/research/7")
        assert r.status_code == 204
        assert r.content == b""
        assert client.get("/api/research/7").status_code == 404

    def test_delete_missing_is_404_with_same_format_as_get(self, monkeypatch):
        monkeypatch.setattr(research_api, "delete_report", lambda report_id, user_id: False)
        monkeypatch.setattr(research_api, "get_report", lambda report_id, user_id: None)

        r = client.delete("/api/research/12345")
        assert r.status_code == 404
        assert "12345" in r.json()["detail"]
        assert r.json() == client.get("/api/research/12345").json()

    def test_db_error_on_delete_is_503(self, monkeypatch):
        def boom(report_id, user_id):
            raise OperationalError("DELETE", {}, Exception("connection refused"))

        monkeypatch.setattr(research_api, "delete_report", boom)
        r = client.delete("/api/research/7")
        assert r.status_code == 503
        assert "데이터베이스" in r.json()["detail"]

    @pytest.mark.parametrize("report_id", ["0", "abc", "99999999999999999999"])
    def test_delete_bad_id_is_422(self, report_id):
        assert client.delete(f"/api/research/{report_id}").status_code == 422

    @pytest.mark.integration
    def test_delete_also_removes_tool_call_logs(self, dev_db, make_user, login_as):
        from sqlalchemy import text

        from app.reports import delete_report, save_report

        records = [
            {"tool_name": "stock_tool", "arguments": {"ticker": "삼성전자"}, "result": {"found": False},
             "called_at": datetime.now(timezone.utc), "elapsed_ms": 1},
            {"tool_name": "news_tool", "arguments": {"company_name": "삼성전자"}, "result": {"found": False},
             "called_at": datetime.now(timezone.utc), "elapsed_ms": 1},
        ]
        user, _ = make_user()
        login_as(user.id)
        report_id = save_report("[pytest] DELETE cascade 확인용 임시 리포트", "임시 답변", records, user_id=user.id)
        assert report_id is not None
        try:
            count_logs = text("SELECT count(*) FROM tool_call_log WHERE report_id = :id")
            assert dev_db.execute(count_logs, {"id": report_id}).scalar() == 2
            dev_db.rollback()

            assert client.delete(f"/api/research/{report_id}").status_code == 204

            assert dev_db.execute(count_logs, {"id": report_id}).scalar() == 0
            assert dev_db.execute(
                text("SELECT count(*) FROM research_report WHERE id = :id"), {"id": report_id}
            ).scalar() == 0
            assert client.get(f"/api/research/{report_id}").status_code == 404
        finally:
            dev_db.rollback()
            delete_report(report_id, user_id=user.id)


class TestFollowUp:
    PREV = {"report_id": 5, "question": "삼성전자 최근 주가 어때?", "answer": "삼성전자는 최근 3일간 하락했습니다."}

    def test_unknown_previous_report_is_404_same_format_as_get(self, monkeypatch):
        called = []
        monkeypatch.setattr(research_api, "get_report", lambda report_id, user_id: None)
        monkeypatch.setattr(research_api, "ask_question", lambda *a, **k: called.append(1))
        r = client.post("/api/research", json={"question": "그럼 뉴스는?", "previous_report_id": 12345})
        assert r.status_code == 404
        assert "12345" in r.json()["detail"]
        assert r.json() == client.get("/api/research/12345").json()
        assert called == []  # 이전 보고서가 없으면 LLM을 부르지 않는다

    def test_without_previous_id_agent_is_called_exactly_as_before(self, monkeypatch):
        calls = []
        monkeypatch.setattr(research_api, "ask_question", lambda *a, **k: calls.append((a, k)) or OK_RESULT)
        monkeypatch.setattr(research_api, "get_report", lambda *a, **k: pytest.fail("get_report를 부르면 안 됨"))
        assert post("삼성전자 주가").status_code == 200
        assert calls == [(("삼성전자 주가",), {"user_id": USER_ID})]

    def test_null_previous_id_is_same_as_omitted(self, monkeypatch):
        calls = []
        monkeypatch.setattr(research_api, "ask_question", lambda *a, **k: calls.append((a, k)) or OK_RESULT)
        r = client.post("/api/research", json={"question": "삼성전자 주가", "previous_report_id": None})
        assert r.status_code == 200 and calls == [(("삼성전자 주가",), {"user_id": USER_ID})]

    def test_previous_report_is_passed_to_agent_and_echoed(self, monkeypatch):
        calls = []

        def fake(question, previous=None, user_id=None):
            calls.append((question, previous, user_id))
            return {**OK_RESULT, "report_id": 9, "previous_report_id": previous["report_id"]}

        def fake_get(report_id, user_id):
            return self.PREV if (report_id, user_id) == (5, USER_ID) else None

        monkeypatch.setattr(research_api, "get_report", fake_get)
        monkeypatch.setattr(research_api, "ask_question", fake)
        r = client.post("/api/research", json={"question": "  그럼 최근 뉴스는?  ", "previous_report_id": 5})
        assert r.status_code == 200
        assert r.json()["report_id"] == 9 and r.json()["previous_report_id"] == 5
        assert calls == [("그럼 최근 뉴스는?", self.PREV, USER_ID)]

    def test_previous_report_of_another_user_is_404_and_agent_not_called(self, monkeypatch, login_as):
        """이어 쓰기 대상은 본인 리포트만. 남의 리포트 id는 없는 리포트와 같은 404다."""
        login_as(2)
        seen = []
        monkeypatch.setattr(research_api, "get_report", lambda report_id, user_id: seen.append(user_id) or None)
        monkeypatch.setattr(research_api, "ask_question", lambda *a, **k: pytest.fail("호출되면 안 됨"))
        r = client.post("/api/research", json={"question": "그럼 뉴스는?", "previous_report_id": 5})
        assert r.status_code == 404
        assert seen == [2]

    @pytest.mark.parametrize("bad", [0, -1, "abc", 99999999999999999999, 1.5])
    def test_bad_previous_id_is_422(self, monkeypatch, bad):
        monkeypatch.setattr(research_api, "ask_question", lambda *a, **k: pytest.fail("호출되면 안 됨"))
        r = client.post("/api/research", json={"question": "그럼 뉴스는?", "previous_report_id": bad})
        assert r.status_code == 422
        assert r.json()["detail"][0]["loc"] == ["body", "previous_report_id"]

    def test_db_error_while_loading_previous_is_503(self, monkeypatch):
        def boom(report_id, user_id):
            raise OperationalError("SELECT", {}, Exception("connection refused"))

        monkeypatch.setattr(research_api, "get_report", boom)
        r = client.post("/api/research", json={"question": "그럼 뉴스는?", "previous_report_id": 5})
        assert r.status_code == 503 and "데이터베이스" in r.json()["detail"]

    def test_detail_and_list_expose_previous_report_id(self, monkeypatch):
        detail = {
            "report_id": 8, "previous_report_id": 5, "question": "q", "answer": "a", "summary": None,
            "company_name": None, "created_at": "2026-09-28T00:00:00+00:00", "used_tools": [], "sources": [],
        }
        monkeypatch.setattr(research_api, "get_report", lambda report_id, user_id: detail)
        assert client.get("/api/research/8").json()["previous_report_id"] == 5
        keys = ("report_id", "previous_report_id", "question", "summary", "company_name", "created_at", "used_tools")
        monkeypatch.setattr(
            research_api, "list_reports", lambda user_id, limit, offset: (1, [{k: detail[k] for k in keys}])
        )
        assert client.get("/api/research").json()["items"][0]["previous_report_id"] == 5

    @pytest.mark.integration
    def test_previous_report_id_is_stored_and_survives_parent_delete(self, dev_db, make_user, login_as):
        from sqlalchemy import text

        from app.reports import delete_report, get_report, save_report

        user, _ = make_user()
        login_as(user.id)
        parent = save_report("[pytest] 부모 리포트", "부모 답변", [], user_id=user.id)
        child = save_report("[pytest] 후속 리포트", "후속 답변", [], previous_report_id=parent, user_id=user.id)
        try:
            assert get_report(child, user_id=user.id)["previous_report_id"] == parent
            assert get_report(parent, user_id=user.id)["previous_report_id"] is None
            assert client.get(f"/api/research/{child}").json()["previous_report_id"] == parent

            assert client.delete(f"/api/research/{parent}").status_code == 204
            # 직전 보고서가 삭제돼도 후속 보고서는 남고 연결만 끊어진다(ON DELETE SET NULL)
            assert get_report(child, user_id=user.id)["previous_report_id"] is None
            row = dev_db.execute(text("SELECT count(*) FROM research_report WHERE id = :i"), {"i": child})
            assert row.scalar() == 1
        finally:
            dev_db.rollback()
            delete_report(child, user_id=user.id)
            delete_report(parent, user_id=user.id)
