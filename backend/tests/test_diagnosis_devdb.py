"""포트폴리오 진단을 실제 dev DB + 실제 토큰으로 검증한다(가격과 LLM만 고정). 만든 사용자는 끝나면 지운다."""

import json

import pytest
from fastapi.testclient import TestClient

import app.diagnosis as d
import app.portfolio as portfolio_module
from app.db.session import SessionLocal
from app.diagnosis import RETURN_WINDOW_DAYS, load_inputs
from app.main import app
from app.models import Company, StockPrice
from app.models.virtual_account import INITIAL_BALANCE

client = TestClient(app, raise_server_exceptions=False)

pytestmark = pytest.mark.integration

PRICE = 1000.0
LLM_OK = {"summary": "현금 비중을 확인했습니다.", "strengths": ["분산"], "risks": ["없음"], "suggestions": ["점검"]}


@pytest.fixture(autouse=True)
def fixed_price_and_llm(monkeypatch):
    def price(db, company):
        return {"current_price": PRICE, "change_amount": 0.0, "change_pct": 0.0, "as_of": "t",
                "is_realtime": True, "source": "naver"}

    monkeypatch.setattr(portfolio_module, "get_realtime_price_data", price)
    monkeypatch.setattr(d, "get_realtime_price_data", price)
    calls = []
    monkeypatch.setattr(d, "_chat", lambda m: calls.append(m) or json.dumps(LLM_OK, ensure_ascii=False))
    return calls


def _buy(user, ticker, qty):
    r = client.post("/api/portfolio/orders", json={"ticker": ticker, "side": "buy", "quantity": qty},
                    headers=user["headers"])
    assert r.status_code == 200, r.text


def _diagnose(user):
    return client.post("/api/portfolio/diagnosis", headers=user["headers"])


def _expected_20d(ticker):
    db = SessionLocal()
    try:
        cid = db.query(Company.id).filter(Company.ticker == ticker).scalar()
        closes = [float(c) for (c,) in db.query(StockPrice.close_price).filter(StockPrice.company_id == cid)
                  .order_by(StockPrice.price_date.desc()).limit(RETURN_WINDOW_DAYS + 1)]
    finally:
        db.close()
    if len(closes) < RETURN_WINDOW_DAYS + 1:
        return None
    return round((closes[0] / closes[RETURN_WINDOW_DAYS] - 1) * 100, 2)


class TestDiagnosisDevDB:
    def test_after_buying_returns_full_structure(self, signup, fixed_price_and_llm):
        user = signup()
        _buy(user, "005930", 3)   # KOSPI
        _buy(user, "247540", 2)   # KOSDAQ(에코프로비엠)
        r = _diagnose(user)
        assert r.status_code == 200, r.text
        body = r.json()

        assert body["source"] == "llm" and len(fixed_price_and_llm) == 1
        m = body["metrics"]
        assert m["cash_balance"] == INITIAL_BALANCE - 5 * PRICE
        assert m["stock_eval_amount"] == 5 * PRICE and m["total_asset"] == INITIAL_BALANCE
        assert m["holding_count"] == 2 and m["priced_holding_count"] == 2
        assert m["market_weights_pct"] == {"KOSDAQ": 40.0, "KOSPI": 60.0}
        assert m["total_profit_loss"] == 0 and m["total_profit_loss_pct"] == 0.0

        by_ticker = {h["ticker"]: h for h in body["holdings"]}
        assert set(by_ticker) == {"005930", "247540"}
        assert by_ticker["005930"]["market"] == "KOSPI" and by_ticker["247540"]["market"] == "KOSDAQ"
        assert by_ticker["005930"]["avg_price"] == PRICE and by_ticker["005930"]["eval_amount"] == 3 * PRICE
        # 20거래일 수익률은 stock_price 종가로 계산한다
        for t in by_ticker:
            assert by_ticker[t]["return_20d_pct"] == _expected_20d(t)
        assert {f["code"] for f in body["flags"]} == {"FEW_HOLDINGS", "CASH_HIGH"}

    def test_no_holdings_is_400_without_llm(self, signup, fixed_price_and_llm):
        user = signup()
        r = _diagnose(user)
        assert r.status_code == 400 and "보유 종목이 없습니다" in r.json()["detail"]
        assert fixed_price_and_llm == []

    def test_after_selling_everything_is_400(self, signup, fixed_price_and_llm):
        user = signup()
        _buy(user, "005930", 1)
        assert client.post("/api/portfolio/orders", json={"ticker": "005930", "side": "sell", "quantity": 1},
                           headers=user["headers"]).status_code == 200
        assert _diagnose(user).status_code == 400

    def test_users_do_not_mix(self, signup):
        a, b = signup(), signup()
        _buy(a, "005930", 2)
        _buy(b, "000660", 7)
        ha = [h["ticker"] for h in _diagnose(a).json()["holdings"]]
        hb = [(h["ticker"], h["quantity"]) for h in _diagnose(b).json()["holdings"]]
        assert ha == ["005930"] and hb == [("000660", 7)]

    def test_load_inputs_reads_only_owner_rows(self, signup):
        a, b = signup(), signup()
        _buy(a, "005930", 2)
        _buy(b, "000660", 7)
        db = SessionLocal()
        try:
            cash, inputs = load_inputs(db, f"user:{a['id']}")
        finally:
            db.close()
        assert cash == INITIAL_BALANCE - 2 * PRICE
        assert [(i.ticker, i.quantity) for i in inputs] == [("005930", 2)]

    def test_diagnosis_does_not_change_portfolio(self, signup):
        user = signup()
        _buy(user, "005930", 2)
        before = client.get("/api/portfolio", headers=user["headers"]).json()
        trades_before = client.get("/api/portfolio/trades", headers=user["headers"]).json()
        assert _diagnose(user).status_code == 200
        assert client.get("/api/portfolio", headers=user["headers"]).json() == before
        assert client.get("/api/portfolio/trades", headers=user["headers"]).json() == trades_before
