"""GET /api/portfolio/exposure 라우트 테스트(DB 없이). DB 계층(get_exposure)을 소유자 키별로 다른 입력을 돌려주는 가짜로 바꿔서
인증(422보다 먼저), 쿼리 범위, 소유자 격리, 보유 없음, 응답 필드 계약을 확인한다. 실제 DB 대조는 test_exposure_devdb.py가 한다."""

from datetime import date, datetime

import pytest
from fastapi.testclient import TestClient

import app.api.exposure as exposure_api
from app.briefing import KST
from app.exposure import ConstituentIn, EtfIn, HoldingIn, compute_exposure
from app.main import app

client = TestClient(app, raise_server_exceptions=False)

D = date(2026, 10, 29)
NOW = datetime(2026, 10, 30, 16, 0, tzinfo=KST)
NONE = type(None)

ETF = EtfIn("102110", "TIGER 200", D, [
    ConstituentIn("삼성전자", "005930", 35.0, "005930", "삼성전자"), ConstituentIn("SK하이닉스", "000660", 25.0, "000660", "SK하이닉스"),
    ConstituentIn("Apple Inc", "AAPL US EQUITY", 5.0),
])
# user:7은 삼성전자 + TIGER 200 + 레버리지 ETF, user:8은 NAVER만, user:9는 보유 없음.
OWNERS = {
    "user:7": ([HoldingIn("005930", "삼성전자", "stock", 10, 70_000.0, D), HoldingIn("102110", "TIGER 200", "etf", 100, 30_000.0, D),
                HoldingIn("122630", "KODEX 레버리지", "etf", 10, 20_000.0, D)], {"102110": ETF}),
    "user:8": ([HoldingIn("035420", "NAVER", "stock", 3, 200_000.0, D)], {}),
    "user:9": ([], {}),
}


@pytest.fixture(autouse=True)
def fake_service(monkeypatch):
    calls = []

    def fake(db, owner_key, limit, now=None):
        calls.append((owner_key, limit))
        holdings, etfs = OWNERS[owner_key]
        return {"generated_at": NOW.isoformat(), "stale": False, "stale_notice": None, **compute_exposure(holdings, etfs, NOW.date(), limit)}

    monkeypatch.setattr(exposure_api, "get_exposure", fake)
    return calls


class TestAuth:
    def test_missing_token_is_401(self, fake_service):
        r = client.get("/api/portfolio/exposure")
        assert r.status_code == 401 and r.headers["www-authenticate"] == "Bearer"
        assert fake_service == []

    def test_garbage_token_and_device_header_are_401(self, fake_service):
        assert client.get("/api/portfolio/exposure", headers={"Authorization": "Bearer nope"}).status_code == 401
        assert client.get("/api/portfolio/exposure", headers={"X-Device-Id": "abc"}).status_code == 401
        assert fake_service == []

    @pytest.mark.parametrize("q", ["limit=0", "limit=51", "limit=abc"])
    def test_auth_error_comes_before_validation_error(self, q, fake_service):
        assert client.get(f"/api/portfolio/exposure?{q}").status_code == 401
        assert fake_service == []


class TestQuery:
    @pytest.fixture(autouse=True)
    def _in(self, login_as):
        login_as(7)

    def test_default_limit_is_10(self, fake_service):
        assert client.get("/api/portfolio/exposure").status_code == 200
        assert fake_service == [("user:7", 10)]

    @pytest.mark.parametrize("value", [1, 2, 50])
    def test_limit_bounds_accepted(self, value, fake_service):
        r = client.get("/api/portfolio/exposure", params={"limit": value})
        assert r.status_code == 200 and fake_service == [("user:7", value)]

    @pytest.mark.parametrize("value", ["0", "51", "-1", "abc", "1.5", ""])
    def test_limit_out_of_range_is_422(self, value, fake_service):
        assert client.get("/api/portfolio/exposure", params={"limit": value}).status_code == 422
        assert fake_service == []

    def test_limit_cuts_companies_only(self):
        body = client.get("/api/portfolio/exposure", params={"limit": 1}).json()
        assert [c["name"] for c in body["companies"]] == ["삼성전자"] and body["limit"] == 1
        assert body["summary"]["company_count"] == 3  # 삼성전자, SK하이닉스, Apple Inc: limit은 목록만 자른다


class TestContract:
    @pytest.fixture(autouse=True)
    def _in(self, login_as):
        login_as(7)

    def test_fields_and_types(self):
        body = client.get("/api/portfolio/exposure").json()
        assert list(body) == ["generated_at", "valuation_date", "stale", "stale_notice", "etf_holdings_stale", "message", "limit", "summary",
                              "companies", "overlaps", "decomposed_etfs", "undecomposed_etfs", "flags", "data_warnings", "disclaimer"]
        assert isinstance(body["generated_at"], str) and isinstance(body["stale"], bool) and isinstance(body["limit"], int)
        assert isinstance(body["valuation_date"], str) and isinstance(body["stale_notice"], NONE) and isinstance(body["message"], NONE)
        assert list(body["summary"]) == ["total_eval_amount", "holding_count", "stock_amount", "etf_amount", "decomposed_etf_amount",
                                         "undecomposed_etf_amount", "companies_amount", "other_amount", "other_weight_pct", "company_count"]
        assert all(isinstance(v, (int, float)) for v in body["summary"].values())
        c = body["companies"][0]
        assert list(c) == ["rank", "name", "code", "linked", "direct_amount", "via_etf_amount", "via_etfs", "total_amount",
                           "effective_weight_pct", "direct_weight_pct"]
        assert isinstance(c["rank"], int) and isinstance(c["linked"], bool) and isinstance(c["via_etfs"], list)
        assert list(c["via_etfs"][0]) == ["ticker", "name", "amount", "etf_weight_pct"]
        assert list(body["decomposed_etfs"][0]) == ["ticker", "name", "eval_amount", "weight_pct", "holdings_as_of", "stale", "stale_notice",
                                                    "constituent_count", "weight_sum_pct"]
        assert list(body["undecomposed_etfs"][0]) == ["ticker", "name", "eval_amount", "weight_pct", "reason_code", "reason"]
        assert list(body["flags"][0]) == ["kind", "name", "code", "value_pct", "threshold_pct", "message"]
        assert "투자 권유나 자문이 아니며" in body["disclaimer"]

    def test_unlinked_company_has_provider_code_or_null(self):
        body = client.get("/api/portfolio/exposure", params={"limit": 50}).json()
        apple = next(c for c in body["companies"] if c["name"] == "Apple Inc")
        assert apple["linked"] is False and apple["code"] == "AAPL US EQUITY"

    def test_samsung_effective_weight_and_overlap(self):
        # 전체 = 700,000 + 3,000,000 + 200,000(레버리지, 분해 안 함) = 3,900,000. 삼성전자 = 700,000 + 3,000,000 x 35% = 1,750,000
        body = client.get("/api/portfolio/exposure").json()
        s = body["companies"][0]
        assert s["name"] == "삼성전자" and s["total_amount"] == 1_750_000 and s["effective_weight_pct"] == 44.87
        assert [o["name"] for o in body["overlaps"]] == ["삼성전자"]
        assert body["undecomposed_etfs"][0]["ticker"] == "122630" and body["undecomposed_etfs"][0]["reason_code"] == "leveraged_inverse"
        assert {f["kind"] for f in body["flags"]} == {"concentration", "etf_inflates_weight"}

    def test_no_secrets_or_internal_keys(self):
        text = client.get("/api/portfolio/exposure").text
        for bad in ("device_id", "owner_key", "password", "token", "user:7"):
            assert bad not in text


class TestOwnerIsolation:
    def test_uses_the_logged_in_users_owner_key_only(self, login_as, fake_service):
        login_as(7)
        a = client.get("/api/portfolio/exposure").json()
        login_as(8)
        b = client.get("/api/portfolio/exposure").json()
        assert fake_service == [("user:7", 10), ("user:8", 10)]
        assert "NAVER" not in str(a) and "삼성전자" not in str(b) and "TIGER" not in str(b)
        assert [c["name"] for c in b["companies"]] == ["NAVER"] and b["companies"][0]["effective_weight_pct"] == 100.0

    def test_owner_key_cannot_be_chosen_by_the_client(self, login_as, fake_service):
        login_as(8)
        client.get("/api/portfolio/exposure", params={"owner_key": "user:7", "device_id": "user:7"}, headers={"X-Device-Id": "user:7"})
        assert fake_service == [("user:8", 10)]


class TestEmptyState:
    def test_no_holdings_is_200_with_message(self, login_as):
        login_as(9)
        r = client.get("/api/portfolio/exposure")
        assert r.status_code == 200
        body = r.json()
        assert body["message"] == "보유 중인 종목이 없습니다. 모의투자로 종목이나 ETF를 매수하면 기업별 실질 노출을 계산합니다."
        assert body["companies"] == [] and body["overlaps"] == [] and body["decomposed_etfs"] == [] and body["undecomposed_etfs"] == []
        assert body["flags"] == [] and body["summary"]["total_eval_amount"] == 0 and body["valuation_date"] is None
        assert "투자 권유나 자문이 아니며" in body["disclaimer"]
