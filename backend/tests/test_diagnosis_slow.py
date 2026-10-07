"""포트폴리오 진단을 실제 Ollama로 1회 호출한다(구조만 검증, 품질/문장 내용은 판단하지 않는다).

실행: pytest -m slow tests/test_diagnosis_slow.py
LLM 출력이 검증에 걸리면 규칙 기반으로 폴백하는 것도 정상 동작이므로 source는 둘 중 하나면 된다.
"""

import pytest

import app.diagnosis as d
from app.diagnosis import HoldingInput, build_diagnosis

pytestmark = pytest.mark.slow


def test_real_ollama_call_returns_valid_structure(monkeypatch):
    calls = []
    real_chat = d._chat

    def counting(messages):
        calls.append(1)
        return real_chat(messages)

    monkeypatch.setattr(d, "_chat", counting)
    out = build_diagnosis(
        4_000_000.0,
        [
            HoldingInput("005930", "삼성전자", "KOSPI", 10, 280000.0, 271250.0, True, -2.31),
            HoldingInput("035720", "카카오", "KOSPI", 20, 45000.0, 38000.0, True, None),
        ],
    )
    assert 1 <= len(calls) <= d.LLM_MAX_ATTEMPTS  # 실제로 LLM을 불렀다(연결 실패면 1회 후 폴백)
    assert out["source"] in ("llm", "rule_based")
    assert isinstance(out["summary"], str) and out["summary"]
    for key in ("strengths", "risks", "suggestions"):
        assert isinstance(out[key], list) and all(isinstance(s, str) and s for s in out[key])
    if out["source"] == "llm":
        joined = "\n".join([out["summary"], *out["strengths"], *out["risks"], *out["suggestions"]])
        payload = d.build_llm_payload(out["holdings"], out["metrics"], out["flags"])
        assert d.find_disallowed_numbers(joined, d.allowed_numbers(payload)) == []
