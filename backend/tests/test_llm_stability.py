"""(A) LLM 호출 안정성: 호출별 timeout·num_predict, timeout/연결 오류 시 코드 요약 또는 지연 안내(Ollama mock)."""

import httpx
import pytest

from app import agent


@pytest.fixture(autouse=True)
def fresh_state():
    agent._reset_llm_state()
    yield


def _raise(exc):
    def chat(**kw):
        raise exc

    return chat


def test_num_predict_is_always_sent(monkeypatch):
    seen = {}

    def chat(**kw):
        seen.update(kw)
        return {"message": {"content": "ok"}, "done_reason": "stop"}

    monkeypatch.setattr(agent.ollama, "chat", chat)
    agent._llm_chat(model="m", messages=[], options={"temperature": 0})
    assert seen["options"] == {"temperature": 0, "num_predict": agent.LLM_NUM_PREDICT}
    assert agent._meta()["llm_truncated"] is False


def test_truncation_is_recorded(monkeypatch):
    monkeypatch.setattr(agent.ollama, "chat", lambda **kw: {"message": {"content": "..."}, "done_reason": "length"})
    agent._llm_chat(model="m", messages=[])
    assert agent._meta()["llm_truncated"] is True


@pytest.mark.parametrize("exc, reason", [
    (httpx.ReadTimeout("slow"), "timeout"),
    (httpx.ConnectTimeout("slow"), "timeout"),
    (ConnectionError("Failed to connect to Ollama"), "connection"),
    (httpx.RemoteProtocolError("closed"), "connection"),
])
def test_timeout_and_connection_errors_become_llm_unavailable(monkeypatch, exc, reason):
    monkeypatch.setattr(agent.ollama, "chat", _raise(exc))
    with pytest.raises(agent.LLMUnavailable) as e:
        agent._llm_chat(model="m", messages=[])
    assert e.value.reason == reason


def test_question_budget_is_enforced(monkeypatch):
    monkeypatch.setattr(agent.ollama, "chat", lambda **kw: pytest.fail("호출하면 안 됨"))
    agent._STATE.deadline = agent.time.monotonic() - 1
    with pytest.raises(agent.LLMUnavailable) as e:
        agent._llm_chat(model="m", messages=[])
    assert e.value.reason == "question_budget"


def test_library_chat_uses_a_client_with_the_remaining_timeout(monkeypatch):
    """테스트가 ollama.chat을 바꾸지 않았으면(실서비스) 호출별 timeout을 단 Client로 부른다."""
    made = {}

    class FakeClient:
        def __init__(self, timeout=None, **kw):
            made["timeout"] = timeout

        def chat(self, **kw):
            made["options"] = kw["options"]
            return {"message": {"content": "ok"}, "done_reason": "stop"}

    monkeypatch.setattr(agent.ollama, "Client", FakeClient)
    assert agent.ollama.chat is agent._LIBRARY_CHAT
    agent._llm_chat(model="m", messages=[])
    assert 0 < made["timeout"] <= agent.LLM_CALL_TIMEOUT_S
    assert made["options"]["num_predict"] == agent.LLM_NUM_PREDICT


# ---------- _answer의 대체 동작 ----------

def test_first_call_timeout_gives_the_delay_message(monkeypatch):
    monkeypatch.setattr(agent, "_question_companies", lambda q: [])
    monkeypatch.setattr(agent, "_first_call", lambda *a, **k: (_ for _ in ()).throw(agent.LLMUnavailable("timeout")))
    resp, records = agent._answer("투자를 처음 시작하려면 뭐부터 해야 해?", "m")
    assert resp == {"answer": agent.LLM_TIMEOUT_ANSWER, "used_tools": [], "sources": []} and records == []
    assert agent._meta()["fallback"] == "timeout"


def test_final_call_timeout_answers_with_code_summary(monkeypatch):
    stock = {"found": True, "company_name": "삼성전자",
             "prices": [{"price_date": "2026-10-14", "close_price": 235000.0, "change_pct": 0.43, "volume": 100}]}
    call = {"function": {"name": "stock_tool", "arguments": {"ticker": "삼성전자", "period_days": 20}}}
    monkeypatch.setattr(agent, "_question_companies", lambda q: [])
    monkeypatch.setattr(agent, "_first_call", lambda *a, **k: ({"role": "assistant", "content": "", "tool_calls": [call]}, [call]))
    monkeypatch.setattr(agent, "_repair_company_args", lambda *a, **k: {})
    monkeypatch.setattr(agent, "_question_subjects", lambda q, c: [])
    monkeypatch.setattr(agent, "_unresolved_company_answer", lambda *a, **k: None)
    monkeypatch.setattr(agent, "_run_tool", lambda name, args: dict(stock))
    monkeypatch.setattr(agent.ollama, "chat", _raise(httpx.ReadTimeout("slow")))
    resp, records = agent._answer("삼성전자 주가 흐름 알려줘", "m")
    assert resp["answer"].startswith("## 주가 동향\n삼성전자의 2026-10-14 종가는 235,000원입니다.")
    assert resp["used_tools"] == ["stock_tool"] and len(records) == 1
    assert agent._meta()["fallback"] == "timeout"


def test_ask_question_attaches_meta_and_api_returns_200(monkeypatch, login_as):
    from fastapi.testclient import TestClient

    from app.api import research as research_api
    from app.main import app

    monkeypatch.setattr(agent, "_question_companies", lambda q: [])
    monkeypatch.setattr(agent, "_first_call", lambda *a, **k: (_ for _ in ()).throw(agent.LLMUnavailable("connection")))
    monkeypatch.setattr(agent, "save_report_row", lambda *a, **k: None)
    out = agent.ask_question("투자를 처음 시작하려면 뭐부터 해야 해?", user_id=1)
    assert out["answer"] == agent.LLM_TIMEOUT_ANSWER and out["meta"]["fallback"] == "connection"

    login_as()
    monkeypatch.setattr(research_api, "ask_question", agent.ask_question)
    r = TestClient(app).post("/api/research", json={"question": "투자를 처음 시작하려면 뭐부터 해야 해?"})
    assert r.status_code == 200
    body = r.json()
    assert body["answer"] == agent.LLM_TIMEOUT_ANSWER and "meta" not in body  # 내부 메타는 API 응답에 없다


def test_truncated_final_answer_is_replaced_with_code_summary(monkeypatch):
    stock = {"found": True, "company_name": "TIGER 200",
             "prices": [{"price_date": "2026-10-14", "close_price": 95070.0, "change_pct": -0.5, "volume": 10}]}
    call = {"function": {"name": "stock_tool", "arguments": {"ticker": "TIGER 200", "period_days": 20}}}
    monkeypatch.setattr(agent, "_question_companies", lambda q: [])
    monkeypatch.setattr(agent, "_first_call", lambda *a, **k: ({"role": "assistant", "content": "", "tool_calls": [call]}, [call]))
    monkeypatch.setattr(agent, "_repair_company_args", lambda *a, **k: {})
    monkeypatch.setattr(agent, "_question_subjects", lambda q, c: [])
    monkeypatch.setattr(agent, "_unresolved_company_answer", lambda *a, **k: None)
    monkeypatch.setattr(agent, "_run_tool", lambda name, args: dict(stock))
    looped = "TIGER 200의 현재 가격은 95,070원입니다. " * 200
    monkeypatch.setattr(agent.ollama, "chat", lambda **kw: {"message": {"content": looped}, "done_reason": "length"})
    resp, _ = agent._answer("TIGER 200 흐름 알려줘", "m")
    assert resp["answer"].startswith("## 주가 동향\nTIGER 200의 2026-10-14 종가는 95,070원입니다.")
    assert resp["answer"].count("95,070원") == 1
    assert agent._meta()["fallback"] == "length"
