"""ETF 1단계를 실제 dev DB + 실제 토큰으로 검증한다(체결가와 LLM만 고정). dev DB에 ETF 50종목이 수집돼 있어야 한다
(`python -m scripts.build_etf_universe` -> `collect_prices --universe universe_etf50.json`). 없으면 건너뛴다.
만든 사용자는 끝나면 지운다(ETF company 행과 시세/뉴스는 데이터라서 지우지 않는다)."""

import json
import secrets

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

import app.diagnosis as d
import app.portfolio as portfolio_module
from app.collectors import get_or_create_company
from app.db.session import SessionLocal
from app.discovery import get_trending
from app.main import app
from app.models import Company
from app.models.virtual_account import INITIAL_BALANCE
from app.tools.company_resolver import extract_from_text, resolve_company

client = TestClient(app, raise_server_exceptions=False)

pytestmark = pytest.mark.integration

PRICE = 1000.0
REPRESENTATIVE = {
    "069500": "KODEX 200", "360750": "TIGER 미국S&P500", "133690": "TIGER 미국나스닥100",
    "122630": "KODEX 레버리지", "114800": "KODEX 인버스",
}
LLM_OK = {"summary": "현금 비중을 확인했습니다.", "suggestions": ["종목별 뉴스를 점검"]}


@pytest.fixture(autouse=True)
def etf_data(dev_db):
    dev_db.rollback()
    if dev_db.execute(text("SELECT count(*) FROM company WHERE asset_type = 'etf'")).scalar() < 5:
        pytest.skip("dev DB에 ETF가 수집돼 있지 않음")


@pytest.fixture
def fixed_price_and_llm(monkeypatch):
    def price(db, company):
        return {"current_price": PRICE, "change_amount": 0.0, "change_pct": 0.0, "as_of": "t",
                "is_realtime": True, "source": "naver"}

    monkeypatch.setattr(portfolio_module, "get_realtime_price_data", price)
    monkeypatch.setattr(d, "get_realtime_price_data", price)
    monkeypatch.setattr(d, "_chat", lambda m: json.dumps(LLM_OK, ensure_ascii=False))


class TestSchemaAndData:
    def test_asset_type_column_is_not_null_with_default_stock(self, dev_db):
        row = dev_db.execute(text(
            "SELECT is_nullable, column_default, character_maximum_length FROM information_schema.columns "
            "WHERE table_name = 'company' AND column_name = 'asset_type'")).one()
        assert row[0] == "NO" and "stock" in row[1] and row[2] == 10

    def test_only_stock_and_etf_are_allowed(self, dev_db):
        with pytest.raises(IntegrityError):
            dev_db.execute(text("INSERT INTO company (ticker, name, asset_type) VALUES ('ZZ0001', 'bad type', 'bond')"))
        dev_db.rollback()
        with pytest.raises(IntegrityError):
            dev_db.execute(text("INSERT INTO company (ticker, name, asset_type) VALUES ('ZZ0002', 'null type', NULL)"))
        dev_db.rollback()

    def test_existing_companies_are_stock_and_etfs_are_marked(self, dev_db):
        assert dev_db.execute(text("SELECT asset_type FROM company WHERE ticker = '005930'")).scalar() == "stock"
        types = dict(dev_db.execute(text("SELECT asset_type, count(*) FROM company GROUP BY 1")).all())
        assert set(types) == {"stock", "etf"} and types["stock"] >= 300

    def test_representative_etfs_are_registered_as_etf(self, dev_db):
        rows = dict(dev_db.execute(text("SELECT ticker, name FROM company WHERE asset_type = 'etf'")).all())
        for ticker, name in REPRESENTATIVE.items():
            assert rows.get(ticker) == name

    def test_etf_rows_have_no_market_sector_or_disclosures_but_have_prices_and_news(self, dev_db):
        q = lambda s: dev_db.execute(text(s)).scalar()
        assert q("SELECT count(*) FROM company WHERE asset_type = 'etf' AND (market IS NOT NULL OR sector IS NOT NULL)") == 0
        assert q("SELECT count(*) FROM disclosure WHERE company_id IN (SELECT id FROM company WHERE asset_type = 'etf')") == 0
        assert q("SELECT count(*) FROM company c WHERE asset_type = 'etf' AND NOT EXISTS "
                 "(SELECT 1 FROM stock_price sp WHERE sp.company_id = c.id)") == 0
        assert q("SELECT count(*) FROM news WHERE company_id IN (SELECT id FROM company WHERE asset_type = 'etf')") > 0

    def test_get_or_create_company_on_real_db(self, dev_db):
        suffix = secrets.token_hex(2).upper()
        tickers = [f"ZE{suffix}", f"ZS{suffix}"]
        try:
            db = SessionLocal()
            try:
                etf = get_or_create_company(db, tickers[0], f"테스트ETF{suffix}", asset_type="etf")
                stock = get_or_create_company(db, tickers[1], f"테스트주식{suffix}")
                db.commit()
                assert (etf.asset_type, stock.asset_type) == ("etf", "stock")
                # 이미 있는 회사는 기본값으로 덮어쓰지 않는다
                again = get_or_create_company(db, tickers[0], f"테스트ETF{suffix}")
                assert again.asset_type == "etf"
            finally:
                db.close()
        finally:
            dev_db.rollback()
            dev_db.execute(text("DELETE FROM company WHERE ticker = ANY(:t)"), {"t": tickers})
            dev_db.commit()


class TestSearch:
    def test_etf_filter_returns_only_etfs(self):
        items = client.get("/api/companies", params={"asset_type": "etf", "limit": 300}).json()
        assert len(items) >= 5 and {i["asset_type"] for i in items} == {"etf"}
        assert all(i["market"] is None and i["sector"] is None for i in items)

    def test_stock_filter_excludes_etfs_and_matches_the_old_listing(self):
        stocks = client.get("/api/companies", params={"asset_type": "stock", "limit": 300}).json()
        assert len(stocks) == 300 and {i["asset_type"] for i in stocks} == {"stock"}
        assert stocks[0]["ticker"] == "005930"  # 기존 순서 그대로(id 순)

    def test_default_without_filter_is_unchanged_for_the_first_page(self):
        first = client.get("/api/companies").json()
        assert all(i["asset_type"] == "stock" for i in first)  # 상위 50개는 id 순이라 주식만 나온다
        assert len(client.get("/api/companies", params={"limit": 300}).json()) == 300  # 기존 응답 한도는 그대로 300

    def test_no_filter_search_finds_both_kinds(self):
        items = client.get("/api/companies", params={"q": "삼성전자", "limit": 100}).json()
        kinds = {i["asset_type"] for i in items}
        assert kinds == {"stock", "etf"}  # 삼성전자(주식) + 이름에 삼성전자가 든 ETF
        assert items[0]["ticker"] == "005930"

    @pytest.mark.parametrize("q, ticker", [
        ("코덱스200", "069500"), ("KODEX 200", "069500"), ("kodex200", "069500"), ("069500", "069500"),
        ("코덱스 레버리지", "122630"), ("코덱스 인버스", "114800"), ("타이거 나스닥", "133690"),
    ])
    def test_search_finds_etf_by_brand_name(self, q, ticker):
        items = client.get("/api/companies", params={"q": q, "asset_type": "etf"}).json()
        assert ticker in [i["ticker"] for i in items], q

    def test_etf_item_has_latest_close(self):
        (item,) = [i for i in client.get("/api/companies", params={"q": "069500", "asset_type": "etf"}).json()
                   if i["ticker"] == "069500"]
        assert item["name"] == "KODEX 200" and item["latest_close"] and item["change_pct"] is not None

    def test_invalid_filter_is_422(self):
        assert client.get("/api/companies", params={"asset_type": "bond"}).status_code == 422


class TestResolveOnRealData:
    @pytest.mark.parametrize("raw, name", [
        ("KODEX 200", "KODEX 200"), ("코덱스200", "KODEX 200"), ("코덱스 200", "KODEX 200"), ("타이거 나스닥", "TIGER 미국나스닥100"),
        ("TIGER 미국S&P500", "TIGER 미국S&P500"), ("타이거 미국 S&P500", "TIGER 미국S&P500"), ("코덱스 레버리지", "KODEX 레버리지"),
        ("KODEX 인버스", "KODEX 인버스"), ("069500", "KODEX 200"), ("0167A0", "SOL AI반도체TOP2플러스"),
    ])
    def test_etf_inputs(self, dev_db, raw, name):
        res = resolve_company(dev_db, raw)
        assert res.company is not None and res.company.name == name and res.company.asset_type == "etf", res.message

    @pytest.mark.parametrize("raw, name", [
        ("삼성전자", "삼성전자"), ("삼전", "삼성전자"), ("네이버", "NAVER"), ("현대자동차", "현대차"), ("카뱅", "카카오뱅크"),
        ("SK하이닉스", "SK하이닉스"), ("005930", "삼성전자"), ("sk 하이닉스", "SK하이닉스"),
    ])
    def test_stock_inputs_are_unchanged(self, dev_db, raw, name):
        res = resolve_company(dev_db, raw)
        assert res.company is not None and res.company.name == name and res.company.asset_type == "stock"

    def test_every_stock_name_still_resolves_to_itself(self, dev_db):
        for c in dev_db.query(Company).filter(Company.asset_type == "stock"):
            assert resolve_company(dev_db, c.name).company.id == c.id, c.name

    def test_extract_from_question(self, dev_db):
        def names(q):
            return [c.name for c in extract_from_text(dev_db, q)]

        assert names("KODEX 200 최근 흐름 알려줘") == ["KODEX 200"]
        assert names("TIGER 미국나스닥100 어때?") == ["TIGER 미국나스닥100"]
        assert names("KODEX 레버리지 위험해?") == ["KODEX 레버리지"]
        assert names("코덱스200이랑 삼성전자 비교해줘") == ["KODEX 200", "삼성전자"]
        assert names("삼성전자 주가 알려줘") == ["삼성전자"]
        assert names("카카오뱅크 어때?") == ["카카오뱅크"]


class TestDiscovery:
    def _tickers(self, dev_db, asset_type):
        return {t for (t,) in dev_db.execute(text("SELECT ticker FROM company WHERE asset_type = :a"), {"a": asset_type})}

    @pytest.mark.parametrize("category", ["gainers", "losers", "volume_surge"])
    def test_default_is_stock_only_and_not_empty(self, dev_db, category):
        r = get_trending(dev_db, category, limit=50)
        assert r["found"] and r["asset_type"] == "stock"
        etfs = self._tickers(dev_db, "etf")
        assert not {i["ticker"] for i in r["items"]} & etfs
        if category != "volume_surge":
            assert r["items"], "ETF가 더 최근까지 수집돼도 주식 결과는 비면 안 된다"

    def test_stock_latest_date_is_not_pulled_forward_by_etf_dates(self, dev_db):
        stock_max = dev_db.execute(text(
            "SELECT MAX(sp.price_date) FROM stock_price sp JOIN company c ON c.id = sp.company_id WHERE c.asset_type = 'stock'")).scalar()
        assert get_trending(dev_db, "gainers", 5)["price_date"] == stock_max.isoformat()

    def test_etf_option_returns_only_etfs_with_their_own_date(self, dev_db):
        etf_max = dev_db.execute(text(
            "SELECT MAX(sp.price_date) FROM stock_price sp JOIN company c ON c.id = sp.company_id WHERE c.asset_type = 'etf'")).scalar()
        r = get_trending(dev_db, "gainers", 50, asset_type="etf")
        assert r["found"] and r["asset_type"] == "etf" and r["price_date"] == etf_max.isoformat()
        assert r["items"] and {i["ticker"] for i in r["items"]} <= self._tickers(dev_db, "etf")

    def test_api_default_and_etf(self):
        a = client.get("/api/discovery/trending", params={"limit": 50}).json()
        b = client.get("/api/discovery/trending", params={"limit": 50, "asset_type": "etf"}).json()
        assert a["asset_type"] == "stock" and b["asset_type"] == "etf"
        assert {i["ticker"] for i in a["items"]}.isdisjoint({i["ticker"] for i in b["items"]})


class TestTradingFlow:
    def _h(self, user):
        return user["headers"]

    def test_search_watchlist_buy_sell_reset_with_an_etf(self, signup, fixed_price_and_llm):
        user = signup()
        h = self._h(user)

        # 검색 -> 관심종목(중복은 409)
        found = client.get("/api/companies", params={"q": "코덱스200", "asset_type": "etf"}).json()
        assert found[0]["ticker"] == "069500" and "KODEX 200TR" in [i["name"] for i in found]  # 부분 일치라 200TR 등도 나온다
        r = client.post("/api/watchlist", json={"ticker": "코덱스200"}, headers=h)
        assert r.status_code == 201 and r.json()["asset_type"] == "etf" and r.json()["ticker"] == "069500"
        assert r.json()["latest_close"] is not None
        assert client.post("/api/watchlist", json={"ticker": "KODEX 200"}, headers=h).status_code == 409
        wl = client.get("/api/watchlist", headers=h).json()["items"]
        assert [(i["ticker"], i["asset_type"]) for i in wl] == [("069500", "etf")]

        # 매수 -> 포트폴리오/체결 내역
        r = client.post("/api/portfolio/orders", json={"ticker": "KODEX 200", "side": "buy", "quantity": 2}, headers=h)
        assert r.status_code == 200, r.text
        assert r.json()["ticker"] == "069500" and r.json()["price"] == PRICE
        pf = client.get("/api/portfolio", headers=h).json()
        assert pf["cash_balance"] == INITIAL_BALANCE - 2 * PRICE
        assert [(x["ticker"], x["asset_type"], x["quantity"]) for x in pf["holdings"]] == [("069500", "etf", 2)]
        trades = client.get("/api/portfolio/trades", headers=h).json()
        assert [(t["ticker"], t["asset_type"], t["side"]) for t in trades["items"]] == [("069500", "etf", "buy")]
        assert client.get("/api/portfolio/trades", params={"ticker": "코덱스200"}, headers=h).json()["total"] == 1

        # 일부 매도 -> 전량 매도 -> 초과 매도(400)
        assert client.post("/api/portfolio/orders", json={"ticker": "069500", "side": "sell", "quantity": 1}, headers=h).status_code == 200
        assert client.get("/api/portfolio", headers=h).json()["holdings"][0]["quantity"] == 1
        assert client.post("/api/portfolio/orders", json={"ticker": "069500", "side": "sell", "quantity": 1}, headers=h).status_code == 200
        assert client.get("/api/portfolio", headers=h).json()["holdings"] == []
        assert client.post("/api/portfolio/orders", json={"ticker": "069500", "side": "sell", "quantity": 1}, headers=h).status_code == 400
        assert client.get("/api/portfolio", headers=h).json()["cash_balance"] == INITIAL_BALANCE

        # 관심종목 삭제, 초기화
        assert client.delete("/api/watchlist/069500", headers=h).status_code == 204
        assert client.get("/api/watchlist", headers=h).json()["items"] == []
        client.post("/api/portfolio/orders", json={"ticker": "TIGER 200", "side": "buy", "quantity": 3}, headers=h)
        r = client.post("/api/portfolio/reset", json={"confirm": True}, headers=h)
        assert r.status_code == 200 and r.json()["holdings"] == [] and r.json()["cash_balance"] == INITIAL_BALANCE
        assert client.get("/api/portfolio/trades", headers=h).json()["total"] == 0

    def test_stock_trading_is_unchanged_and_marked_as_stock(self, signup, fixed_price_and_llm):
        user = signup()
        h = self._h(user)
        assert client.post("/api/portfolio/orders", json={"ticker": "삼성전자", "side": "buy", "quantity": 2}, headers=h).status_code == 200
        assert client.post("/api/portfolio/orders", json={"ticker": "코덱스200", "side": "buy", "quantity": 1}, headers=h).status_code == 200
        pf = client.get("/api/portfolio", headers=h).json()
        assert [(x["ticker"], x["asset_type"]) for x in pf["holdings"]] == [("005930", "stock"), ("069500", "etf")]
        assert {t["ticker"]: t["asset_type"] for t in client.get("/api/portfolio/trades", headers=h).json()["items"]} == {
            "005930": "stock", "069500": "etf"}
        wl = client.post("/api/watchlist", json={"ticker": "삼성전자"}, headers=h).json()
        assert wl["asset_type"] == "stock"

    def test_unknown_etf_is_404(self, signup):
        user = signup()
        assert client.post("/api/watchlist", json={"ticker": "코덱스 없는상품"}, headers=self._h(user)).status_code == 404
        assert client.post("/api/portfolio/orders", json={"ticker": "코덱스 없는상품", "side": "buy", "quantity": 1},
                           headers=self._h(user)).status_code == 404

    def test_users_do_not_see_each_others_etf_positions(self, signup, fixed_price_and_llm):
        a, b = signup(), signup()
        client.post("/api/portfolio/orders", json={"ticker": "069500", "side": "buy", "quantity": 5}, headers=a["headers"])
        assert client.get("/api/portfolio", headers=b["headers"]).json()["holdings"] == []

    def test_diagnosis_with_an_etf_holding_runs_without_error(self, signup, fixed_price_and_llm):
        """진단 로직은 이번 단계에서 바꾸지 않았다. ETF 보유(market NULL)여도 에러 없이 도는지만 확인한다."""
        user = signup()
        h = self._h(user)
        client.post("/api/portfolio/orders", json={"ticker": "KODEX 200", "side": "buy", "quantity": 3}, headers=h)
        r = client.post("/api/portfolio/diagnosis", headers=h)
        assert r.status_code == 200, r.text
        body = r.json()
        assert [x["ticker"] for x in body["holdings"]] == ["069500"]
        # 2단계: ETF는 시장 쏠림 계산에서 빠지고(market "ETF"), ETF를 보유하면 종목 수 부족 경고를 하지 않는다
        assert body["holdings"][0]["market"] == "ETF" and body["metrics"]["market_weights_pct"] == {}
        assert not {"MARKET_SKEW", "FEW_HOLDINGS"} & {f["code"] for f in body["flags"]}
        assert body["diagnosis_id"] is not None
