from datetime import date

import pytest
import requests

import app.realtime_price as rp
from app.models import Company, StockPrice
from app.realtime_price import _fallback_from_db, _fetch_from_naver, get_realtime_price_data

TICKER = "005930"
COMPANY = Company(id=1, ticker=TICKER, name="삼성전자", market="KOSPI")


def naver_payload(market_status="OPEN", **overrides):
    row = {
        "closePrice": "268,000",
        "closePriceRaw": "268000",
        "compareToPreviousClosePriceRaw": "-500",
        "fluctuationsRatioRaw": "-0.19",
        "localTradedAt": "2026-10-01T11:14:11.124728+09:00",
        "marketStatus": market_status,
        **overrides,
    }
    return {"pollingInterval": 7000, "datas": [row], "time": "20261001111411"}


class FakeResponse:
    def __init__(self, json_data, status_code=200):
        self._json = json_data
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code}")

    def json(self):
        return self._json


class _StockPriceQuery:
    """realtime_price._fallback_from_db가 쓰는 db.query(StockPrice)... 체인을 흉내낸다."""

    def __init__(self, row):
        self._row = row

    def filter(self, *a, **k):
        return self

    def order_by(self, *a, **k):
        return self

    def first(self):
        return self._row


class FakeDB:
    def __init__(self, row=None):
        self._row = row

    def query(self, model):
        assert model is StockPrice
        return _StockPriceQuery(self._row)


class TestFetchFromNaver:
    def test_parses_successful_open_market_response(self, monkeypatch):
        monkeypatch.setattr(rp.requests, "get", lambda *a, **k: FakeResponse(naver_payload()))
        data = _fetch_from_naver(TICKER)
        assert data == {
            "current_price": 268000.0,
            "change_amount": -500.0,
            "change_pct": -0.19,
            "as_of": "2026-10-01T11:14:11.124728+09:00",
            "is_realtime": True,
            "source": "naver",
        }

    def test_empty_datas_is_none(self, monkeypatch):
        monkeypatch.setattr(
            rp.requests, "get", lambda *a, **k: FakeResponse({"pollingInterval": 70000, "datas": [], "time": "x"})
        )
        assert _fetch_from_naver("999999") is None

    @pytest.mark.parametrize("status", ["CLOSE", "PRE", "AFTER", None, ""])
    def test_market_not_open_is_none(self, monkeypatch, status):
        monkeypatch.setattr(rp.requests, "get", lambda *a, **k: FakeResponse(naver_payload(market_status=status)))
        assert _fetch_from_naver(TICKER) is None

    def test_network_error_is_none_not_raised(self, monkeypatch):
        def boom(*a, **k):
            raise requests.ConnectionError("boom")

        monkeypatch.setattr(rp.requests, "get", boom)
        assert _fetch_from_naver(TICKER) is None

    def test_timeout_is_none(self, monkeypatch):
        def boom(*a, **k):
            raise requests.Timeout("timed out")

        monkeypatch.setattr(rp.requests, "get", boom)
        assert _fetch_from_naver(TICKER) is None

    def test_http_error_status_is_none(self, monkeypatch):
        monkeypatch.setattr(rp.requests, "get", lambda *a, **k: FakeResponse({}, status_code=503))
        assert _fetch_from_naver(TICKER) is None

    def test_invalid_json_is_none(self, monkeypatch):
        class BadJsonResponse(FakeResponse):
            def json(self):
                raise ValueError("not json")

        monkeypatch.setattr(rp.requests, "get", lambda *a, **k: BadJsonResponse({}))
        assert _fetch_from_naver(TICKER) is None

    def test_missing_expected_field_is_none(self, monkeypatch):
        payload = naver_payload()
        del payload["datas"][0]["closePriceRaw"]
        monkeypatch.setattr(rp.requests, "get", lambda *a, **k: FakeResponse(payload))
        assert _fetch_from_naver(TICKER) is None

    def test_passes_timeout_and_ticker_in_url(self, monkeypatch):
        seen = {}

        def fake_get(url, headers=None, timeout=None):
            seen.update(url=url, timeout=timeout)
            return FakeResponse(naver_payload())

        monkeypatch.setattr(rp.requests, "get", fake_get)
        _fetch_from_naver(TICKER)
        assert seen["url"] == rp.NAVER_REALTIME_URL.format(ticker=TICKER)
        assert seen["timeout"] == rp.REQUEST_TIMEOUT_SECONDS


class TestFallbackFromDb:
    def test_uses_latest_stock_price_row(self):
        row = StockPrice(
            company_id=1, price_date=date(2026, 9, 23), close_price=71000, volume=1000, change_pct=1.23
        )
        data = _fallback_from_db(FakeDB(row), COMPANY)
        assert data == {
            "current_price": 71000.0,
            "change_amount": None,
            "change_pct": 1.23,
            "as_of": "2026-09-23",
            "is_realtime": False,
            "source": "fallback",
        }

    def test_no_rows_is_none(self):
        assert _fallback_from_db(FakeDB(None), COMPANY) is None

    def test_null_change_pct_is_none(self):
        row = StockPrice(company_id=1, price_date=date(2026, 9, 23), close_price=71000, volume=1000, change_pct=None)
        data = _fallback_from_db(FakeDB(row), COMPANY)
        assert data["change_pct"] is None


class TestGetRealtimePriceDataCaching:
    @pytest.fixture(autouse=True)
    def clear_cache(self):
        rp._cache.clear()
        yield
        rp._cache.clear()

    def test_naver_success_is_cached_and_not_refetched(self, monkeypatch):
        calls = []

        def fake_fetch(ticker):
            calls.append(ticker)
            return {
                "current_price": 268000.0, "change_amount": -500.0, "change_pct": -0.19,
                "as_of": "t", "is_realtime": True, "source": "naver",
            }

        monkeypatch.setattr(rp, "_fetch_from_naver", fake_fetch)
        db = FakeDB(None)

        first = get_realtime_price_data(db, COMPANY)
        second = get_realtime_price_data(db, COMPANY)

        assert first == second
        assert first["is_realtime"] is True
        assert calls == [TICKER]  # 두 번째 호출은 캐시를 써서 네이버를 다시 부르지 않는다

    def test_cache_expires_after_ttl(self, monkeypatch):
        calls = []
        monkeypatch.setattr(
            rp, "_fetch_from_naver",
            lambda t: calls.append(t) or {
                "current_price": 1.0, "change_amount": 0.0, "change_pct": 0.0,
                "as_of": "t", "is_realtime": True, "source": "naver",
            },
        )
        db = FakeDB(None)
        clock = {"t": 1000.0}
        monkeypatch.setattr(rp.time, "monotonic", lambda: clock["t"])

        get_realtime_price_data(db, COMPANY)
        clock["t"] += rp.CACHE_TTL_SECONDS + 0.01
        get_realtime_price_data(db, COMPANY)

        assert calls == [TICKER, TICKER]  # TTL이 지나면 다시 호출한다

    def test_naver_failure_falls_back_to_db_and_flags_not_realtime(self, monkeypatch):
        monkeypatch.setattr(rp, "_fetch_from_naver", lambda t: None)
        row = StockPrice(company_id=1, price_date=date(2026, 9, 23), close_price=71000, volume=1, change_pct=0.5)

        data = get_realtime_price_data(FakeDB(row), COMPANY)

        assert data["is_realtime"] is False
        assert data["source"] == "fallback"
        assert data["current_price"] == 71000.0

    def test_fallback_result_is_also_cached(self, monkeypatch):
        naver_calls = []
        monkeypatch.setattr(rp, "_fetch_from_naver", lambda t: naver_calls.append(t))
        row = StockPrice(company_id=1, price_date=date(2026, 9, 23), close_price=71000, volume=1, change_pct=0.5)
        db = FakeDB(row)

        get_realtime_price_data(db, COMPANY)
        get_realtime_price_data(db, COMPANY)

        assert naver_calls == [TICKER]  # 폴백도 캐시되어 두 번째 호출은 네이버를 다시 부르지 않는다

    def test_both_sources_unavailable_returns_none_and_is_not_cached(self, monkeypatch):
        monkeypatch.setattr(rp, "_fetch_from_naver", lambda t: None)
        assert get_realtime_price_data(FakeDB(None), COMPANY) is None
        assert TICKER not in rp._cache
