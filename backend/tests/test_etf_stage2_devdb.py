"""ETF 2단계를 실제 dev DB로 검증한다(가격과 LLM만 고정): tool의 ETF 결과, ETF 뉴스 이름 필터, 에이전트 보조 함수,
진단(ETF 단독 / 주식+레버리지)과 진단 이력 비교에서 새 플래그의 추가·해소. dev DB에 ETF가 수집돼 있어야 한다."""

import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

import app.agent as agent
import app.diagnosis as d
import app.portfolio as portfolio_module
from app.etf import mentions_etf_name
from app.main import app
from app.models import News
from app.tools.disclosure_tool import disclosure_tool
from app.tools.market_tool import market_data_is_current, market_tool
from app.tools.news_tool import news_tool
from app.tools.stock_tool import stock_tool

client = TestClient(app, raise_server_exceptions=False)

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def etf_data(dev_db):
    dev_db.rollback()
    if dev_db.execute(text("SELECT count(*) FROM company WHERE asset_type = 'etf'")).scalar() < 5:
        pytest.skip("dev DB에 ETF가 수집돼 있지 않음")


@pytest.fixture
def fixed(monkeypatch):
    def price(db, company):
        return {"current_price": 1000.0, "change_amount": 0.0, "change_pct": 0.0, "as_of": "t", "is_realtime": True, "source": "naver"}

    monkeypatch.setattr(portfolio_module, "get_realtime_price_data", price)
    monkeypatch.setattr(d, "get_realtime_price_data", price)
    monkeypatch.setattr(d, "_chat", lambda m: json.dumps({"summary": "현금 비중을 확인했습니다.", "suggestions": ["점검"]}, ensure_ascii=False))


class TestToolsOnEtf:
    def test_disclosure_tool_explains_why(self):
        r = disclosure_tool("KODEX 200")
        assert r["found"] is False and r["asset_type"] == "etf"
        assert r["message"].startswith("KODEX 200은 ETF(상장지수펀드)입니다.") and "수집하지 않습니다" in r["message"]
        assert "아직 수집된 공시가 없습니다" not in r["message"]

    def test_market_tool_explains_why(self):
        r = market_tool("TIGER 미국나스닥100", 5)
        assert r["found"] is False and r["asset_type"] == "etf" and "시장 지수와 비교하지 않습니다" in r["message"]
        assert market_data_is_current("KODEX 200") is False  # 원인 질문에서 코드가 market_tool을 덧붙이지 않는다

    @pytest.mark.needs_collected_data
    def test_stock_tools_unchanged(self):
        assert market_tool("삼성전자", 5)["found"] is True
        assert "asset_type" not in disclosure_tool("삼성전자") and "asset_type" not in news_tool("삼성전자")

    @pytest.mark.needs_collected_data
    def test_news_tool_returns_only_articles_naming_the_etf(self, dev_db):
        names = [n for (n,) in dev_db.execute(text("SELECT name FROM company WHERE asset_type = 'etf'"))]
        for name in ("KODEX 200", "KODEX 레버리지", "TIGER 미국나스닥100", "RISE 200"):
            r = news_tool(name, 5)
            assert r["found"] and r["asset_type"] == "etf" and "excluded_unrelated" in r
            for n in r["news"]:
                assert mentions_etf_name(f"{n['title']} {n['content']}", name, [x for x in names if x != name]), (name, n["title"])

    @pytest.mark.needs_collected_data
    def test_stock_news_tool_returns_the_same_rows_as_before(self, dev_db):
        cid = dev_db.execute(text("SELECT id FROM company WHERE ticker = '005930'")).scalar()
        expected = [u for (u,) in dev_db.query(News.url).filter(News.company_id == cid).order_by(News.published_at.desc()).limit(5)]
        assert [n["url"] for n in news_tool("삼성전자", 5)["news"]] == expected

    @pytest.mark.needs_collected_data
    def test_stored_etf_news_all_name_the_etf(self, dev_db):
        rows = dev_db.execute(text(
            "SELECT c.name, n.title, coalesce(n.content, '') FROM news n JOIN company c ON c.id = n.company_id "
            "WHERE c.asset_type = 'etf'")).all()
        names = {r[0] for r in rows}
        hits = sum(mentions_etf_name(f"{t} {c}", name, names - {name}) for name, t, c in rows)
        assert rows and hits / len(rows) >= 0.8  # 2단계 재수집 목표(80%) 이상


class TestAgentHelpersOnRealDb:
    def test_question_subjects(self):
        assert agent._question_subjects("KODEX 200이랑 삼성전자 비교", []) == [("KODEX 200", "etf"), ("삼성전자", "stock")]
        assert agent._question_subjects("코덱스 레버리지 위험해?", []) == [("KODEX 레버리지", "etf")]
        assert agent._question_subjects("삼성전자 주가", []) == [("삼성전자", "stock")]
        calls = [{"function": {"name": "stock_tool", "arguments": {"ticker": "069500"}}}]
        assert agent._question_subjects("이거 어때?", calls) == [("KODEX 200", "etf")]

    def test_add_etf_tools_resolves_existing_calls(self):
        calls = [{"function": {"name": "stock_tool", "arguments": {"ticker": "코덱스200", "period_days": 1}}}]
        added = agent._add_etf_tools(calls, [("KODEX 200", "etf")])
        assert [calls[i]["function"]["name"] for i in added] == ["news_tool"]  # '코덱스200' 호출은 이미 KODEX 200의 주가 조회다

    @pytest.mark.needs_collected_data
    def test_stock_tool_with_20_days_on_etf(self):
        r = stock_tool("KODEX 200", 20)
        assert r["found"] and r["trading_days"] == 20 and "period_change_pct" in r


class TestDiagnosisFlow:
    def _buy(self, user, ticker, qty, side="buy"):
        r = client.post("/api/portfolio/orders", json={"ticker": ticker, "side": side, "quantity": qty}, headers=user["headers"])
        assert r.status_code == 200, r.text

    def test_etf_only_account(self, signup, fixed):
        user = signup()
        self._buy(user, "KODEX 200", 5)
        body = client.post("/api/portfolio/diagnosis", headers=user["headers"]).json()
        assert body["holdings"][0]["market"] == "ETF" and body["holdings"][0]["asset_type"] == "etf"
        assert not {"MARKET_SKEW", "FEW_HOLDINGS", "LEVERAGED_INVERSE_ETF"} & {f["code"] for f in body["flags"]}
        assert any("ETF가 1개 포함돼 있습니다" in s for s in body["strengths"])
        assert not any("KODEX 200는" in s for s in body["strengths"] + body["risks"] + body["notes"])

    def test_leverage_flag_added_and_resolved_in_history_compare(self, signup, fixed):
        user = signup()
        h = user["headers"]
        self._buy(user, "삼성전자", 2)
        first = client.post("/api/portfolio/diagnosis", headers=h).json()
        self._buy(user, "KODEX 레버리지", 3)
        second = client.post("/api/portfolio/diagnosis", headers=h).json()
        codes = {f["code"] for f in second["flags"]}
        assert "LEVERAGED_INVERSE_ETF" in codes and "FEW_HOLDINGS" not in codes
        cmp_ = client.get(f"/api/portfolio/diagnosis/{second['diagnosis_id']}/compare", headers=h).json()
        assert [(f["code"], f["ticker"]) for f in cmp_["changes"]["flags"]["added"] if f["code"] == "LEVERAGED_INVERSE_ETF"] == [
            ("LEVERAGED_INVERSE_ETF", "122630")]
        assert ("FEW_HOLDINGS", None) in [(f["code"], f["ticker"]) for f in cmp_["changes"]["flags"]["resolved"]]

        self._buy(user, "KODEX 레버리지", 3, side="sell")
        third = client.post("/api/portfolio/diagnosis", headers=h).json()
        cmp_ = client.get(f"/api/portfolio/diagnosis/{third['diagnosis_id']}/compare", headers=h).json()
        assert [(f["code"], f["ticker"]) for f in cmp_["changes"]["flags"]["resolved"] if f["code"] == "LEVERAGED_INVERSE_ETF"] == [
            ("LEVERAGED_INVERSE_ETF", "122630")]
        # 주식만 보유했던 첫 진단에는 종목 수 경고가 그대로 있다(주식만이면 이전 규칙)
        assert "FEW_HOLDINGS" in {f["code"] for f in first["flags"]}
        assert "FEW_HOLDINGS" in {f["code"] for f in third["flags"]}
