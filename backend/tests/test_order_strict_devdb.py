"""주문 종목 해석 엄격 모드를 실제 dev DB(company 350여 개)로 검증한다: 유사 이름은 주문되지 않고, 다른 경로의 보정은 그대로다.

테스트 계정은 `captest_` + 랜덤 이름으로 가입하고(signup 픽스처가 끝에 정리), 가격은 고정값으로 monkeypatch해 외부 호출이 없다.
"""

import secrets

import pytest
from fastapi.testclient import TestClient

import app.api.stocks as stocks_api
import app.portfolio as portfolio_module
from app.db.session import SessionLocal
from app.main import app
from app.models import Holding, Trade, VirtualAccount
from app.models.virtual_account import INITIAL_BALANCE
from app.tools.company_resolver import resolve_company

client = TestClient(app, raise_server_exceptions=False)
pytestmark = pytest.mark.integration

PRICE = {"current_price": 1000.0, "change_amount": 0.0, "change_pct": 0.0, "as_of": "t", "is_realtime": True,
         "source": "naver"}


@pytest.fixture(autouse=True)
def fixed_price(monkeypatch):
    monkeypatch.setattr(portfolio_module, "get_realtime_price_data", lambda db, company: PRICE)
    monkeypatch.setattr(stocks_api, "get_realtime_price_data", lambda db, company: PRICE)


@pytest.fixture
def user(signup):
    return signup(f"captest_{secrets.token_hex(6)}")


def _order(user, ticker, quantity=1, side="buy"):
    return client.post("/api/portfolio/orders", json={"ticker": ticker, "side": side, "quantity": quantity},
                       headers=user["headers"])


def _state(dev_db, user):
    dev_db.rollback()
    key = f"user:{user['id']}"
    account = dev_db.get(VirtualAccount, key)
    return {
        "cash": None if account is None else float(account.cash_balance),
        "trades": dev_db.query(Trade).filter(Trade.device_id == key).count(),
        "holdings": dev_db.query(Holding).filter(Holding.device_id == key).count(),
    }


class TestResolverAgainstRealCompanies:
    @pytest.mark.parametrize(
        "raw, suggested",
        [("삼성SD1", "삼성SDI"), ("삼성SDK", "삼성SDI"), ("SK하이닉쓰", "SK하이닉스"), ("현대모비쓰", "현대모비스"),
         ("JB금융", "JB금융지주"), ("삼성전자우", "삼성전자")],
    )
    def test_similar_names_are_rejected_in_strict_mode_only(self, dev_db, raw, suggested):
        strict = resolve_company(dev_db, raw, allow_fuzzy=False)
        assert strict.company is None and suggested in strict.candidates
        assert strict.message.startswith("정확한 종목을 찾지 못했습니다. 혹시 ")

    @pytest.mark.parametrize("raw, expected", [("삼성SD1", "삼성SDI"), ("SK하이닉쓰", "SK하이닉스"), ("현대모비쓰", "현대모비스")])
    def test_default_mode_still_corrects(self, dev_db, raw, expected):
        res = resolve_company(dev_db, raw)
        assert res.company.name == expected and res.corrected_from == raw

    @pytest.mark.parametrize(
        "raw, expected",
        [("005930", "삼성전자"), ("삼성전자", "삼성전자"), ("삼성 전자", "삼성전자"), ("sk 하이닉스", "SK하이닉스"),
         ("삼성sdi", "삼성SDI"), ("kb 금융", "KB금융"), ("네이버", "NAVER"), ("삼성SDS", "삼성에스디에스")],
    )
    def test_exact_spacing_and_alias_are_accepted_in_strict_mode(self, dev_db, raw, expected):
        assert resolve_company(dev_db, raw, allow_fuzzy=False).company.name == expected


class TestOrderEndpoint:
    @pytest.mark.parametrize("raw, suggested", [("삼성SD1", "삼성SDI"), ("SK하이닉쓰", "SK하이닉스"), ("삼성전자우", "삼성전자")])
    def test_similar_name_is_404_and_changes_nothing(self, dev_db, user, raw, suggested):
        r = _order(user, raw)
        assert r.status_code == 404
        assert r.json()["detail"].startswith("정확한 종목을 찾지 못했습니다. 혹시 ") and suggested in r.json()["detail"]
        # 주문이 거부되면 계좌조차 만들어지지 않거나, 만들어졌더라도 초기 상태 그대로다.
        state = _state(dev_db, user)
        assert state["trades"] == 0 and state["holdings"] == 0 and state["cash"] in (None, INITIAL_BALANCE)

    def test_unrelated_name_is_404_without_candidates(self, user):
        r = _order(user, "존재하지않는회사xyz")
        assert r.status_code == 404 and "혹시" not in r.json()["detail"]

    @pytest.mark.parametrize("raw, name", [("005930", "삼성전자"), ("삼성 전자", "삼성전자"), ("삼성SDI", "삼성SDI"),
                                          ("네이버", "NAVER"), ("삼성SDS", "삼성에스디에스")])
    def test_exact_spacing_and_alias_orders_execute_with_same_response_shape(self, user, raw, name):
        r = _order(user, raw)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["company_name"] == name
        assert set(body) == {"ticker", "company_name", "side", "quantity", "price", "executed_at", "cash_balance", "holding"}

    def test_stock_price_path_still_corrects_typos(self, user):
        r = client.get("/api/stocks/삼성SD1/realtime-price")
        assert r.status_code == 200
        assert r.json()["company_name"] == "삼성SDI" and r.json()["corrected_from"] == "삼성SD1"
