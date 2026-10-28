"""GET /api/briefing 라우트 테스트(DB 없이). DB 계층(get_briefing)을 소유자 키별로 다른 입력을 돌려주는 가짜로 바꿔서
인증, 쿼리 범위, 소유자 격리, 빈 사용자, 응답 필드 계약을 확인한다. 실제 DB 대조는 test_briefing_devdb.py가 한다."""

from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

import app.api.briefing as briefing_api
from app.briefing import Bar, BriefingInputs, DisclosureRow, Instrument, KST, NewsRow, build_briefing
from app.main import app

client = TestClient(app, raise_server_exceptions=False)

NOW = datetime(2026, 10, 27, 16, 0, tzinfo=KST)
NONE = type(None)


def closes(prev, last, n=21, volume=1000):
    rows = [Bar(date(2026, 10, 27) - timedelta(days=n - 1 - i), prev, volume) for i in range(n - 1)]
    return rows + [Bar(date(2026, 10, 27), last, volume * 3)]


# 소유자별 입력. user:7은 삼성전자를 보유하고 SK하이닉스와 레버리지 ETF를 관심종목으로 둔다. user:8은 다른 종목을 보유한다.
OWNERS = {
    "user:7": BriefingInputs(
        has_account=True, cash_balance=5_000_000.0,
        holdings=[Instrument("005930", "삼성전자", "stock", 10, 60_000.0)],
        watchlist=[Instrument("000660", "SK하이닉스", "stock"), Instrument("122630", "KODEX 레버리지", "etf")],
        bars={"005930": closes(70_000.0, 74_200.0), "000660": closes(100_000.0, 101_000.0),
              "122630": closes(20_000.0, 19_000.0)},
        news={"005930": [NewsRow("삼성전자 소식", "연합뉴스", "https://n.example/1", NOW - timedelta(hours=3))]},
        disclosures={"005930": [DisclosureRow("분기보고서", "정기공시", "https://d.example/1", NOW - timedelta(days=1))]},
        kospi=[(date(2026, 10, 27), 2550.0), (date(2026, 10, 26), 2500.0)],
        usd_krw=[(date(2026, 10, 27), 1400.0)],
    ),
    "user:8": BriefingInputs(
        has_account=True, cash_balance=1_000_000.0,
        holdings=[Instrument("035420", "NAVER", "stock", 3, 200_000.0)],
        bars={"035420": closes(200_000.0, 210_000.0)},
    ),
    "user:9": BriefingInputs(),
}


@pytest.fixture(autouse=True)
def fake_service(monkeypatch):
    calls = []

    def fake(db, owner_key, news_limit, now=None):
        calls.append((owner_key, news_limit))
        return build_briefing(OWNERS[owner_key], news_limit, NOW)

    monkeypatch.setattr(briefing_api, "get_briefing", fake)
    return calls


class TestAuth:
    def test_missing_token_is_401(self, fake_service):
        r = client.get("/api/briefing")
        assert r.status_code == 401
        assert r.headers["www-authenticate"] == "Bearer"
        assert fake_service == []  # 인증 전에는 데이터를 읽지 않는다

    def test_garbage_token_and_device_header_are_401(self, fake_service):
        assert client.get("/api/briefing", headers={"Authorization": "Bearer nope"}).status_code == 401
        assert client.get("/api/briefing", headers={"X-Device-Id": "abc"}).status_code == 401
        assert fake_service == []


class TestQuery:
    @pytest.fixture(autouse=True)
    def _in(self, login_as):
        login_as(7)

    def test_default_news_limit_is_3(self, fake_service):
        assert client.get("/api/briefing").status_code == 200
        assert fake_service == [("user:7", 3)]

    @pytest.mark.parametrize("value", [1, 5])
    def test_news_limit_bounds_accepted(self, value, fake_service):
        assert client.get("/api/briefing", params={"news_limit": value}).status_code == 200
        assert fake_service == [("user:7", value)]

    @pytest.mark.parametrize("value", ["0", "6", "-1", "abc", "1.5"])
    def test_news_limit_out_of_range_is_422(self, value, fake_service):
        assert client.get("/api/briefing", params={"news_limit": value}).status_code == 422
        assert fake_service == []


class TestOwnerIsolation:
    def test_uses_the_logged_in_users_owner_key_only(self, login_as, fake_service):
        login_as(7)
        a = client.get("/api/briefing").json()
        login_as(8)
        b = client.get("/api/briefing").json()
        assert fake_service == [("user:7", 3), ("user:8", 3)]
        assert [h["ticker"] for h in a["holdings"]] == ["005930"]
        assert [h["ticker"] for h in b["holdings"]] == ["035420"]
        assert "035420" not in str(a) and "005930" not in str(b)
        assert b["watchlist"] == []

    def test_owner_key_cannot_be_chosen_by_the_client(self, login_as, fake_service):
        login_as(8)
        client.get("/api/briefing", params={"owner_key": "user:7", "device_id": "user:7"}, headers={"X-Device-Id": "user:7"})
        assert fake_service == [("user:8", 3)]


class TestEmptyState:
    def test_empty_user(self, login_as):
        login_as(9)
        r = client.get("/api/briefing")
        assert r.status_code == 200
        body = r.json()
        assert body["message"] == "관심종목을 추가하면 개인화된 브리핑을 볼 수 있습니다."
        assert body["holdings"] == [] and body["watchlist"] == [] and body["highlights"] == []
        assert body["portfolio"]["has_account"] is False and body["portfolio"]["cash_balance"] is None
        assert body["market"] == {"kospi": None, "kosdaq": None, "usd_krw": None}


def is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def check(obj, schema, path="$"):
    """schema: {키: 타입 또는 타입 튜플}. NONE이 들어 있으면 null 허용. float는 정수도 받는다. 키 집합이 정확히 같아야 한다."""
    assert set(obj) == set(schema), (path, set(obj) ^ set(schema))
    for key, types in schema.items():
        types = types if isinstance(types, tuple) else (types,)
        v = obj[key]
        if v is None:
            assert NONE in types, f"{path}.{key}는 null이 올 수 없는 필드"
            continue
        ok = any(is_num(v) if t is float else (isinstance(v, t) and not (t is int and isinstance(v, bool)))
                 for t in types if t is not NONE)
        assert ok, f"{path}.{key} = {v!r}, 기대 {types}"


FLAG = {"type": str, "scope": str, "ticker": str, "name": str, "text": str, "value": float, "threshold": float,
        "unit": str, "date": (str, NONE), "direction": (str, NONE)}
QUOTE = {"code": str, "name": str, "date": str, "close": float, "change_pct": (float, NONE)}
ITEM = {
    "ticker": str, "name": str, "asset_type": str, "data_available": bool,
    "latest_close": (float, NONE), "latest_date": (str, NONE),
    "day_change_pct": (float, NONE), "week_change_pct": (float, NONE),
    "volume": (int, NONE), "avg_volume_20d": (float, NONE), "volume_ratio": (float, NONE),
    "news": list, "disclosures": list, "disclosure_count": int, "notice": (str, NONE), "flags": list,
}
HOLDING = {**ITEM, "quantity": int, "avg_price": float, "eval_amount": (float, NONE),
           "weight_pct": (float, NONE), "profit_loss": (float, NONE),
           "profit_loss_pct": (float, NONE), "day_profit_loss": (float, NONE)}


class TestContract:
    @pytest.fixture
    def body(self, login_as):
        login_as(7)
        r = client.get("/api/briefing")
        assert r.status_code == 200
        return r.json()

    def test_top_level(self, body):
        check(body, {
            "generated_at": str, "data_date": (str, NONE), "stale": bool, "stale_notice": (str, NONE),
            "message": (str, NONE), "market": dict, "portfolio": dict, "holdings": list, "watchlist": list,
            "highlights": list, "disclaimer": str,
        })
        assert body["data_date"] == "2026-10-27" and body["stale"] is False and body["stale_notice"] is None
        assert body["disclaimer"]

    def test_market(self, body):
        check(body["market"], {"kospi": (dict, NONE), "kosdaq": (dict, NONE), "usd_krw": (dict, NONE)})
        check(body["market"]["kospi"], QUOTE)
        assert body["market"]["kospi"]["change_pct"] == 2.0
        check(body["market"]["usd_krw"], QUOTE)
        assert body["market"]["usd_krw"]["change_pct"] is None  # 직전 값이 없으면 null
        assert body["market"]["kosdaq"] is None  # 데이터가 없으면 그 항목만 null

    def test_portfolio(self, body):
        check(body["portfolio"], {
            "has_account": bool, "initial_balance": float, "cash_balance": (float, NONE),
            "holdings_value": (float, NONE), "total_asset": (float, NONE),
            "profit_loss": (float, NONE), "profit_loss_pct": (float, NONE),
            "day_profit_loss": (float, NONE), "top_weight": (dict, NONE), "weights": list,
            "message": (str, NONE),
        })
        check(body["portfolio"]["top_weight"], {"ticker": str, "name": str, "weight_pct": float})
        assert body["portfolio"]["initial_balance"] == 10_000_000

    def test_items(self, body):
        (h,) = body["holdings"]
        check(h, HOLDING)
        for w in body["watchlist"]:
            check(w, ITEM)
        check(h["news"][0], {"title": str, "source": (str, NONE), "published_at": str, "url": (str, NONE)})
        check(h["disclosures"][0], {"title": str, "disclosure_type": (str, NONE), "disclosed_at": str,
                                    "source_url": (str, NONE)})
        assert h["day_change_pct"] == 6.0 and h["volume_ratio"] == 3.0
        etf = next(w for w in body["watchlist"] if w["asset_type"] == "etf")
        assert etf["notice"] and "레버리지" in etf["notice"]

    def test_flags_and_highlights(self, body):
        assert body["highlights"] and len(body["highlights"]) <= 5
        for f in body["highlights"] + body["holdings"][0]["flags"]:
            check(f, FLAG)
        assert body["highlights"][0]["scope"] == "holding"
        assert {f["type"] for f in body["holdings"][0]["flags"]} == {"day_move", "volume_spike", "new_disclosure"}

    def test_item_without_data_keeps_the_same_keys(self, login_as, monkeypatch):
        login_as(7)
        inputs = BriefingInputs(watchlist=[Instrument("999999", "무시세", "stock")])
        monkeypatch.setattr(briefing_api, "get_briefing", lambda db, k, n, now=None: build_briefing(inputs, n, NOW))
        (w,) = client.get("/api/briefing").json()["watchlist"]
        check(w, ITEM)
        assert w["data_available"] is False and w["latest_close"] is None and w["flags"] == []
