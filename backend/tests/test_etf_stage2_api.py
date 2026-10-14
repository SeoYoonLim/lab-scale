"""ETF 2단계 라우트(DB·Ollama 없이 mock): 진단 응답의 ETF 항목(market "ETF", asset_type)과 새 플래그 LEVERAGED_INVERSE_ETF가
응답 모델을 그대로 통과하는지, 리서치 라우트가 ETF 답변(레버리지 고정 문구 포함)을 그대로 돌려주는지."""

import json

import pytest
from fastapi.testclient import TestClient

import app.api.portfolio as portfolio_api
import app.api.research as research_api
import app.diagnosis as d
import app.diagnosis_history as history
from app.diagnosis import HoldingInput
from app.main import app

client = TestClient(app, raise_server_exceptions=False)

INPUTS = {
    "user:7": (9_000_000.0, [HoldingInput("069500", "KODEX 200", None, 10, 96_000.0, 96_000.0, True, -13.85, "etf")]),
    "user:8": (9_000_000.0, [
        HoldingInput("005930", "삼성전자", "KOSPI", 2, 263_000.0, 263_000.0, True, -2.59, "stock"),
        HoldingInput("122630", "KODEX 레버리지", None, 3, 83_000.0, 83_000.0, True, -27.21, "etf"),
    ]),
    "user:9": (8_744_300.0, [
        HoldingInput("005930", "삼성전자", "KOSPI", 2, 263_000.0, 263_000.0, False, -2.59),
        HoldingInput("035720", "카카오", "KOSPI", 3, 32_400.0, 32_400.0, False, -11.35),
        HoldingInput("247540", "에코프로비엠", "KOSDAQ", 5, 126_500.0, 126_500.0, False, 18.56),
    ]),
}


@pytest.fixture(autouse=True)
def setup(monkeypatch, login_as):
    monkeypatch.setattr(portfolio_api, "SessionLocal", lambda: type("S", (), {"close": lambda self: None})())
    monkeypatch.setattr(d, "load_inputs", lambda db, key: INPUTS[key])
    monkeypatch.setattr(d, "_chat", lambda m: (_ for _ in ()).throw(ConnectionError("no llm")))  # 규칙 기반 문장
    monkeypatch.setattr(history, "save_diagnosis", lambda uid, body: {"id": 1, "created_at": "2026-10-14T00:00:00+00:00"})
    return login_as


def diagnose(login_as, uid):
    login_as(uid)
    r = client.post("/api/portfolio/diagnosis")
    assert r.status_code == 200, r.text
    return r.json()


class TestDiagnosisEtf:
    def test_etf_only(self, setup):
        body = diagnose(setup, 7)
        h = body["holdings"][0]
        assert h["market"] == "ETF" and h["asset_type"] == "etf"
        assert body["metrics"]["market_weights_pct"] == {}
        assert [f["code"] for f in body["flags"]] == ["CASH_HIGH"]
        assert any("ETF가 1개 포함돼 있습니다" in s for s in body["strengths"])
        assert not any("OTHER" in s for s in body["risks"] + body["strengths"])

    def test_stock_and_leveraged_etf(self, setup):
        body = diagnose(setup, 8)
        flag = next(f for f in body["flags"] if f["code"] == "LEVERAGED_INVERSE_ETF")
        assert flag == {
            "code": "LEVERAGED_INVERSE_ETF",
            "message": f"KODEX 레버리지는 레버리지 ETF로 총자산의 {flag['value']}%입니다. 하루 수익률을 기준으로 움직여 오래 보유하면 "
                       "기초지수 수익률과 달라질 수 있습니다.",
            "value": flag["value"], "threshold": 0.0, "ticker": "122630", "company_name": "KODEX 레버리지", "market": None,
        }
        assert flag["value"] == next(h["weight_pct"] for h in body["holdings"] if h["ticker"] == "122630")
        assert flag["message"] in body["risks"]
        assert body["metrics"]["market_weights_pct"] == {"KOSPI": 100.0}  # 주식(삼성전자)만
        assert "MARKET_SKEW" in {f["code"] for f in body["flags"]}       # 주식이 한 시장뿐이라 그대로 걸린다
        assert "FEW_HOLDINGS" not in {f["code"] for f in body["flags"]}
        assert [h["asset_type"] for h in body["holdings"]] == ["stock", "etf"]

    def test_stock_only_response_keeps_shape_and_values(self, setup):
        body = diagnose(setup, 9)
        assert [f["code"] for f in body["flags"]] == ["CASH_HIGH"]
        assert body["metrics"]["market_weights_pct"] == {"KOSDAQ": 50.37, "KOSPI": 49.63}
        assert {h["market"] for h in body["holdings"]} == {"KOSPI", "KOSDAQ"}
        assert body["strengths"] == ["상위 1종목 비중이 6.33%로 한 종목에 크게 쏠려 있지 않습니다.", "3개 종목에 나눠 보유하고 있습니다."]
        # 조사만 고쳐졌다: 예전 "삼성전자, 카카오, 에코프로비엠는" -> "에코프로비엠은"
        assert body["notes"][0] == "삼성전자, 카카오, 에코프로비엠은 실시간 시세 대신 DB에 저장된 최근 종가로 계산했습니다."


class TestResearchEtf:
    def test_answer_with_notice_is_returned_as_is(self, setup, monkeypatch):
        setup(7)
        answer = "## 주가 동향\nKODEX 레버리지는 20거래일간 -27.21%입니다.\n\n※ KODEX 레버리지는 이름으로 보아 레버리지 ETF입니다. ..."
        monkeypatch.setattr(research_api, "ask_question", lambda q, user_id=None, previous=None: {
            "answer": answer, "used_tools": ["stock_tool", "news_tool"], "sources": [], "report_id": 3, "previous_report_id": None})
        r = client.post("/api/research", json={"question": "KODEX 레버리지 위험해?"})
        assert r.status_code == 200 and r.json()["answer"] == answer and r.json()["used_tools"] == ["stock_tool", "news_tool"]
