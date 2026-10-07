"""POST /api/portfolio/diagnosis 라우트. DB 조회(load_inputs)와 LLM 호출(_chat)을 mock으로 바꿔 끼운다."""

import json

import httpx
import ollama
import pytest
from fastapi.testclient import TestClient

import app.api.portfolio as portfolio_api
import app.diagnosis as d
from app.agent import MODEL_NAME
from app.diagnosis import HoldingInput
from app.disclaimer import DISCLAIMER
from app.main import app

client = TestClient(app, raise_server_exceptions=False)

INPUTS = {
    "user:7": (4_000_000.0, [
        HoldingInput("005930", "삼성전자", "KOSPI", 10, 280000.0, 271250.0, True, -2.31),
        HoldingInput("035720", "카카오", "KOSPI", 20, 45000.0, 38000.0, True, None),
    ]),
    "user:8": (9_000_000.0, [HoldingInput("247540", "에코프로비엠", "KOSDAQ", 5, 150000.0, 160500.0, False, 12.5)]),
}
GOOD = {
    "summary": "총자산은 7,472,500원이고 현금 비중은 53.53%입니다.",
    "strengths": ["삼성전자 비중은 36.3%입니다."],
    "risks": ["카카오의 손익률이 -15.56%로 기준(-10%) 이하입니다."],
    "suggestions": ["카카오를 보유한 이유를 다시 점검해볼 수 있습니다."],
}
TOP_KEYS = {"generated_at", "source", "model", "metrics", "holdings", "flags", "summary", "strengths", "risks",
            "suggestions", "notes", "disclaimer"}


@pytest.fixture(autouse=True)
def setup(monkeypatch, login_as):
    login_as(7)
    monkeypatch.setattr(portfolio_api, "SessionLocal", lambda: type("S", (), {"close": lambda self: None})())
    seen_keys = []

    def fake_load(db, owner_key):
        seen_keys.append(owner_key)
        return INPUTS.get(owner_key, (10_000_000.0, []))

    monkeypatch.setattr(d, "load_inputs", fake_load)
    return seen_keys


def llm(monkeypatch, *responses):
    """_chat을 순서대로 responses를 돌려주는(예외면 raise) 가짜로 바꾸고 호출 기록을 돌려준다."""
    calls = []

    def fake(messages):
        calls.append(messages)
        r = responses[min(len(calls), len(responses)) - 1]
        if isinstance(r, Exception):
            raise r
        return r if isinstance(r, str) else json.dumps(r, ensure_ascii=False)

    monkeypatch.setattr(d, "_chat", fake)
    return calls


def post():
    return client.post("/api/portfolio/diagnosis")


class TestAuth:
    def test_no_token_is_401(self, monkeypatch):
        app.dependency_overrides.clear()
        calls = llm(monkeypatch, GOOD)
        r = post()
        assert r.status_code == 401 and r.headers["www-authenticate"] == "Bearer"
        assert calls == []


class TestLlmPaths:
    def test_valid_json_is_used(self, monkeypatch):
        calls = llm(monkeypatch, GOOD)
        r = post()
        assert r.status_code == 200
        body = r.json()
        assert set(body) == TOP_KEYS
        assert body["source"] == "llm" and body["model"] == MODEL_NAME
        assert {k: body[k] for k in ("summary", "strengths", "risks", "suggestions")} == GOOD
        assert body["disclaimer"] == DISCLAIMER
        assert body["metrics"]["total_asset"] == 7_472_500 and body["metrics"]["holding_count"] == 2
        assert [h["ticker"] for h in body["holdings"]] == ["005930", "035720"]
        assert {f["code"] for f in body["flags"]} == {"FEW_HOLDINGS", "BIG_LOSS", "MARKET_SKEW"}
        assert len(calls) == 1
        assert not any("규칙 기반" in n for n in body["notes"])

    def test_broken_json_retries_once_then_falls_back(self, monkeypatch):
        calls = llm(monkeypatch, "{not json", "still not json")
        body = post().json()
        assert body["source"] == "rule_based" and body["model"] is None
        assert len(calls) == 2
        assert body["risks"] == [f["message"] for f in body["flags"]]
        assert any("규칙 기반 문장" in n for n in body["notes"])

    def test_broken_then_valid_uses_llm(self, monkeypatch):
        calls = llm(monkeypatch, "{not json", GOOD)
        body = post().json()
        assert body["source"] == "llm" and len(calls) == 2

    def test_invented_number_falls_back(self, monkeypatch):
        bad = {**GOOD, "summary": "총자산은 7,500,000원입니다."}
        calls = llm(monkeypatch, bad, bad)
        body = post().json()
        assert body["source"] == "rule_based" and len(calls) == 2
        assert "7,500,000" not in json.dumps(body, ensure_ascii=False)

    def test_missing_key_falls_back(self, monkeypatch):
        bad = {k: v for k, v in GOOD.items() if k != "risks"}
        llm(monkeypatch, bad, bad)
        assert post().json()["source"] == "rule_based"

    def test_direct_trade_instruction_falls_back(self, monkeypatch):
        bad = {**GOOD, "suggestions": ["카카오를 매도하세요."]}
        llm(monkeypatch, bad, bad)
        body = post().json()
        assert body["source"] == "rule_based" and "매도하세요" not in json.dumps(body, ensure_ascii=False)

    @pytest.mark.parametrize(
        "exc",
        [
            ollama.ResponseError("model 'nope' not found", 404),
            ConnectionError("Failed to connect to Ollama"),
            httpx.ReadTimeout("timed out"),
            httpx.ConnectError("refused"),
        ],
        ids=["response_error", "connection", "timeout", "httpx_connect"],
    )
    def test_ollama_failure_falls_back_without_retry(self, monkeypatch, exc):
        calls = llm(monkeypatch, exc, GOOD)
        r = post()
        assert r.status_code == 200  # 503/502가 아니라 폴백
        body = r.json()
        assert body["source"] == "rule_based" and len(calls) == 1
        assert body["summary"] and body["strengths"] and body["risks"] and body["suggestions"]


class TestNoHoldings:
    def test_400_and_llm_not_called(self, monkeypatch, login_as):
        login_as(99)  # INPUTS에 없음 → 보유 없음
        calls = llm(monkeypatch, GOOD)
        r = post()
        assert r.status_code == 400
        assert r.json() == {"detail": "보유 종목이 없습니다. 모의투자 주문 후 진단할 수 있어요."}
        assert calls == []


class TestIsolationAndPrompt:
    def test_each_user_gets_only_own_holdings(self, monkeypatch, login_as, setup):
        llm(monkeypatch, GOOD)
        a = post().json()
        login_as(8)
        llm(monkeypatch, {"summary": "현금 비중은 91.82%입니다.", "strengths": ["a"], "risks": ["b"], "suggestions": ["c"]})
        b = post().json()
        assert setup == ["user:7", "user:8"]
        assert [h["ticker"] for h in a["holdings"]] == ["005930", "035720"]
        assert [h["ticker"] for h in b["holdings"]] == ["247540"]
        assert b["metrics"]["cash_balance"] == 9_000_000

    def test_prompt_has_numbers_but_no_identity(self, monkeypatch, login_as):
        login_as(7, username="secret_name")
        calls = llm(monkeypatch, GOOD)
        post()
        sent = json.dumps(calls[0], ensure_ascii=False)
        assert "secret_name" not in sent and "user:7" not in sent
        payload = json.loads(calls[0][1]["content"])
        assert set(payload) == {"metrics", "flags", "holdings", "return_window_days"}
        assert payload["metrics"]["total_asset"] == 7_472_500

    def test_price_unavailable_is_reported(self, monkeypatch):
        INPUTS["user:7"][1].append(HoldingInput("000660", "SK하이닉스", "KOSPI", 1, 1_800_000.0, None))
        try:
            llm(monkeypatch, GOOD)
            body = post().json()
        finally:
            INPUTS["user:7"][1].pop()
        h = body["holdings"][-1]
        assert h["price_unavailable"] is True and h["eval_amount"] is None
        assert body["metrics"]["holding_count"] == 3 and body["metrics"]["priced_holding_count"] == 2
        assert any("SK하이닉스의 현재가를 가져오지 못해" in n for n in body["notes"])
