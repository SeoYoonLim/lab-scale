"""면책 문구: 상수, 공개 엔드포인트, 리서치 응답(POST/GET 상세에만 추가, 목록에는 없음)."""

import pytest
from fastapi.testclient import TestClient

import app.api.research as research_api
from app.disclaimer import DISCLAIMER
from app.main import app

client = TestClient(app, raise_server_exceptions=False)

EXPECTED = (
    "이 서비스는 학습·시연용 모의투자와 AI 리서치입니다. 제공되는 분석과 의견은 투자 권유나 자문이 아니며, "
    "투자 판단과 그에 따른 손익의 책임은 본인에게 있습니다. AI 응답에는 오류가 있을 수 있습니다."
)
DETAIL = {
    "report_id": 8, "previous_report_id": None, "question": "q", "answer": "a", "summary": None,
    "company_name": None, "created_at": "2026-10-07T00:00:00+00:00", "used_tools": [], "sources": [],
}


def test_constant_text():
    assert DISCLAIMER == EXPECTED


class TestEndpoint:
    def test_public_and_exact(self):
        r = client.get("/api/disclaimer")  # 토큰 없이
        assert r.status_code == 200
        assert r.json() == {"text": EXPECTED}

    def test_only_get(self):
        assert client.post("/api/disclaimer").status_code == 405


class TestResearchResponses:
    @pytest.fixture(autouse=True)
    def _logged_in(self, login_as):
        login_as(1)

    def test_post_has_disclaimer_and_keeps_existing_fields(self, monkeypatch):
        result = {
            "answer": "답변", "used_tools": ["stock_tool"], "sources": [], "report_id": 5, "previous_report_id": None,
        }
        monkeypatch.setattr(research_api, "ask_question", lambda q, user_id=None: result)
        body = client.post("/api/research", json={"question": "삼성전자 주가"}).json()
        assert body == {**result, "disclaimer": EXPECTED}

    def test_detail_has_disclaimer_and_keeps_existing_fields(self, monkeypatch):
        monkeypatch.setattr(research_api, "get_report", lambda report_id, user_id: DETAIL)
        body = client.get("/api/research/8").json()
        assert body == {**DETAIL, "disclaimer": EXPECTED}

    def test_list_does_not_have_disclaimer(self, monkeypatch):
        keys = ("report_id", "previous_report_id", "question", "summary", "company_name", "created_at", "used_tools")
        item = {k: DETAIL[k] for k in keys}
        monkeypatch.setattr(research_api, "list_reports", lambda user_id, limit, offset: (1, [item]))
        body = client.get("/api/research").json()
        assert set(body) == {"total", "items"}
        assert "disclaimer" not in body["items"][0]
        assert body["items"][0] == item

    def test_error_responses_unchanged(self, monkeypatch):
        monkeypatch.setattr(research_api, "get_report", lambda report_id, user_id: None)
        r = client.get("/api/research/12345")
        assert r.status_code == 404 and set(r.json()) == {"detail"}
