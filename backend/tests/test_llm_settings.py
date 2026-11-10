"""Ollama 호출 설정, keep_alive 전달, 워밍업, 평가 스크립트의 모델 옵션. DB·실제 Ollama 없이 돈다."""

import json

import ollama
import pytest

from app import agent, llm_settings as S, warmup
from evals import run_eval as R


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in ("OLLAMA_CHAT_MODEL", "OLLAMA_KEEP_ALIVE", "OLLAMA_THINK", "OLLAMA_WARMUP"):
        monkeypatch.delenv(name, raising=False)


# ---------- 설정 파싱 ----------


def test_chat_model_default_and_override(monkeypatch):
    assert S.chat_model() == "llama3.1:8b"
    monkeypatch.setenv("OLLAMA_CHAT_MODEL", " qwen3:8b ")
    assert S.chat_model() == "qwen3:8b"
    monkeypatch.setenv("OLLAMA_CHAT_MODEL", "")
    assert S.chat_model() == "llama3.1:8b"


def test_keep_alive_default_override_and_blank(monkeypatch):
    assert S.keep_alive() == "30m"
    monkeypatch.setenv("OLLAMA_KEEP_ALIVE", "2h")
    assert S.keep_alive() == "2h"
    monkeypatch.setenv("OLLAMA_KEEP_ALIVE", "")
    assert S.keep_alive() is None
    assert S.call_options() == {} and S.embed_options() == {}


@pytest.mark.parametrize("value, expected", [("true", True), ("1", True), ("false", False), ("off", False), ("", None), ("x", None)])
def test_think_option(monkeypatch, value, expected):
    monkeypatch.setenv("OLLAMA_THINK", value)
    assert S.think_option() is expected


def test_think_is_not_sent_unless_set(monkeypatch):
    assert "think" not in S.call_options()
    monkeypatch.setenv("OLLAMA_THINK", "false")
    assert S.call_options()["think"] is False


# ---------- keep_alive 전달 ----------


def test_llm_chat_sends_keep_alive(monkeypatch):
    seen = {}

    def fake_chat(**kwargs):
        seen.update(kwargs)
        return {"message": {"content": "답"}, "done_reason": "stop"}

    monkeypatch.setattr(ollama, "chat", fake_chat)
    agent._reset_llm_state()
    agent._llm_chat(model="m", messages=[])
    assert seen["keep_alive"] == "30m" and seen["options"]["num_predict"] == agent.LLM_NUM_PREDICT and "think" not in seen


def test_llm_chat_blank_keep_alive_is_not_sent(monkeypatch):
    seen = {}
    monkeypatch.setenv("OLLAMA_KEEP_ALIVE", "")
    monkeypatch.setattr(ollama, "chat", lambda **kw: seen.update(kw) or {"message": {"content": "답"}, "done_reason": "stop"})
    agent._reset_llm_state()
    agent._llm_chat(model="m", messages=[])
    assert "keep_alive" not in seen


def test_query_embedding_sends_keep_alive(monkeypatch):
    from app.tools import rag_search_tool as T

    seen = {}
    monkeypatch.setattr(ollama, "embed", lambda **kw: seen.update(kw) or {"embeddings": [[0.1]]})
    assert T._embed_query("질문") == [0.1]
    assert seen["keep_alive"] == "30m" and seen["model"] == "bge-m3"


# ---------- 워밍업 ----------


class _FakeClient:
    calls = []
    fail = ()

    def __init__(self, timeout=None):
        pass

    def chat(self, **kw):
        _FakeClient.calls.append(("chat", kw))
        if "chat" in _FakeClient.fail:
            raise ConnectionError("no ollama")

    def embed(self, **kw):
        _FakeClient.calls.append(("embed", kw))
        if "embed" in _FakeClient.fail:
            raise ConnectionError("no ollama")


@pytest.fixture
def fake_client(monkeypatch):
    _FakeClient.calls, _FakeClient.fail = [], ()
    monkeypatch.setattr(warmup.ollama, "Client", _FakeClient)
    return _FakeClient


def test_warmup_calls_both_models_with_keep_alive(fake_client):
    warmup.warmup_models()
    assert [c[0] for c in fake_client.calls] == ["chat", "embed"]
    assert fake_client.calls[0][1]["model"] == "llama3.1:8b" and fake_client.calls[0][1]["keep_alive"] == "30m"
    assert fake_client.calls[1][1]["model"] == "bge-m3" and fake_client.calls[1][1]["keep_alive"] == "30m"


@pytest.mark.parametrize("fail", [("chat",), ("embed",), ("chat", "embed")])
def test_warmup_failure_is_ignored_and_tries_the_other_model(fake_client, fail):
    fake_client.fail = fail
    warmup.warmup_models()
    assert [c[0] for c in fake_client.calls] == ["chat", "embed"]


def test_warmup_does_not_run_under_pytest_or_when_disabled(fake_client, monkeypatch):
    assert S.warmup_enabled() is False
    assert warmup.start_warmup() is None
    assert fake_client.calls == []


def test_warmup_disabled_by_env_even_outside_tests(monkeypatch):
    monkeypatch.setattr(S.sys, "modules", {})
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    assert S.warmup_enabled() is True
    monkeypatch.setenv("OLLAMA_WARMUP", "0")
    assert S.warmup_enabled() is False
    monkeypatch.delenv("OLLAMA_WARMUP")
    monkeypatch.setenv("CI", "true")
    assert S.warmup_enabled() is False


def test_start_warmup_returns_a_background_thread(fake_client, monkeypatch):
    monkeypatch.setattr(warmup, "warmup_enabled", lambda: True)
    thread = warmup.start_warmup()
    assert thread.daemon
    thread.join(5)
    assert [c[0] for c in fake_client.calls] == ["chat", "embed"]


# ---------- 평가 스크립트의 모델 옵션(가짜 Ollama) ----------


def test_run_eval_model_option_reaches_live_run_and_summary(monkeypatch, tmp_path):
    seen = {}

    def fake_live(items, repeats, model, log=print, keep_tool_results=False):
        seen.update(model=model, ids=[i["id"] for i in items], keep=keep_tool_results)
        return [], {"eval_user": "captest_x", "deleted_reports": 0}

    monkeypatch.setattr(R, "run_live", fake_live)
    monkeypatch.setattr(R, "ollama_status", lambda model: (seen.update(status=model) or True, "ok"))
    out = tmp_path / "summary_x.json"
    assert R.main(["--mode", "live", "--batch", "10", "--model", "qwen3:8b", "--keep-tool-results", "--out", str(out)]) == 0
    summary = json.loads(out.read_text(encoding="utf-8"))
    assert seen["model"] == seen["status"] == summary["model"] == "qwen3:8b"
    assert seen["keep"] is True and seen["ids"] and all(i.startswith("b10_") for i in seen["ids"])
    assert summary["keep_alive"] == "30m"


def test_run_eval_exclude_batch(monkeypatch, tmp_path):
    seen = {}

    def fake_live(items, repeats, model, log=print, keep_tool_results=False):
        seen["ids"] = [i["id"] for i in items]
        return [], {}

    monkeypatch.setattr(R, "run_live", fake_live)
    monkeypatch.setattr(R, "ollama_status", lambda model: (True, "ok"))
    assert R.main(["--mode", "live", "--split", "holdout", "--exclude-batch", "10", "--out", str(tmp_path / "summary_y.json")]) == 0
    assert seen["ids"] and not any(i.startswith("b10_") for i in seen["ids"])
