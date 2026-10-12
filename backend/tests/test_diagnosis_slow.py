"""포트폴리오 진단을 실제 Ollama로 1회 호출한다(구조만 검증, 품질/문장 내용은 판단하지 않는다).

실행: pytest -m slow tests/test_diagnosis_slow.py
LLM 출력이 검증에 걸리면 규칙 기반으로 폴백하는 것도 정상 동작이므로 source는 둘 중 하나면 된다.
문장 내용은 LLM이 쓴 부분(summary, suggestions)이 아니라 코드가 만든 부분(strengths, risks)만 정확성을 본다.
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


def test_real_ollama_call_never_mispairs_company_and_number():
    """재현 조건(삼성전자 2주, 카카오 3주, 에코프로비엠 5주)에서 종목명과 숫자의 짝이 틀린 문장이 없어야 한다.

    고치기 전에는 3회 중 3회 "비중 상위 1개 종목은 삼성전자"(실제 1위는 에코프로비엠) 같은 문장이 나왔다."""
    out = build_diagnosis(
        8_744_300.0,
        [
            HoldingInput("005930", "삼성전자", "KOSPI", 2, 263000.0, 263000.0, False, -2.59),
            HoldingInput("035720", "카카오", "KOSPI", 3, 32400.0, 32400.0, False, -11.35),
            HoldingInput("247540", "에코프로비엠", "KOSDAQ GLOBAL", 5, 126500.0, 126500.0, False, 18.56),
        ],
    )
    payload = d.build_llm_payload(out["holdings"], out["metrics"], out["flags"])
    per_holding = d.holding_numbers(payload)
    texts = [out["summary"], *out["strengths"], *out["risks"], *out["suggestions"]]
    joined = "\n".join(texts)
    detail = f"source={out['source']} texts={texts}"
    assert d.find_mismatched_numbers(joined, per_holding) == [], detail
    assert d.find_wrong_top_claims(joined, per_holding, d.top_weight_names(payload)) == [], detail
    assert d.find_unknown_companies(joined, per_holding) == [], detail
    assert d.find_disallowed_numbers(joined, d.allowed_numbers(payload)) == [], detail
    if out["source"] == "llm":
        facts = d.fact_sentences(out["holdings"], out["metrics"], out["flags"])
        assert out["strengths"] == facts["strengths"] and out["risks"] == facts["risks"], detail
        assert "에코프로비엠은 총자산의 6.33%이고, 최근 20거래일 주가 등락률은 +18.56%입니다." in out["strengths"]
        assert "카카오는 총자산의 0.97%이고, 최근 20거래일 주가 등락률은 -11.35%입니다." in out["risks"]
