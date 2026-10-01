from datetime import date

import pytest

import app.tools.fx_tool as ft
from app.models import ExchangeRate
from app.tools.fx_tool import fx_tool


class _Q:
    """fx_tool이 쓰는 db.query(ExchangeRate)... 체인을 흉내낸다."""

    def __init__(self, rows):
        self._rows = rows

    def filter(self, *a, **k):
        return self

    def order_by(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    def all(self):
        return list(self._rows)


class FakeDB:
    def __init__(self, rows):
        self._rows = rows

    def query(self, model):
        assert model is ExchangeRate
        return _Q(self._rows)

    def close(self):
        pass


def row(price_date, close_price, change_pct=None):
    return ExchangeRate(pair_code="USD/KRW", price_date=price_date, close_price=close_price, change_pct=change_pct)


D0 = date(2026, 9, 25)


def patch_db(monkeypatch, rows):
    monkeypatch.setattr(ft, "SessionLocal", lambda: FakeDB(rows))


class TestFxToolShape:
    def test_found_result_shape_and_fields(self, monkeypatch):
        # db가 최신순(DESC)으로 주므로 fake rows도 최신이 먼저 오게 둔다(fx_tool이 reversed로 뒤집는다).
        rows = [
            row(date(2026, 9, 29), 1350.50, -0.67),
            row(date(2026, 9, 28), 1359.56, 0.37),
            row(date(2026, 9, 25), 1362.50, 0.90),
        ]
        patch_db(monkeypatch, rows)
        r = fx_tool(3)
        assert r["found"] is True
        assert r["pair_name"] == "원/달러"
        assert r["period_days"] == 3
        assert r["latest_date"] == "2026-09-29"
        assert r["latest_rate"] == 1350.50
        assert r["daily_change_pct"] == -0.67
        assert r["period_change_pct"] == round((1350.50 / 1362.50 - 1) * 100, 2)
        assert r["trend"] == "하락"
        assert [x["price_date"] for x in r["rates"]] == ["2026-09-25", "2026-09-28", "2026-09-29"]

    def test_rising_trend(self, monkeypatch):
        patch_db(monkeypatch, [row(date(2026, 9, 26), 1360.0), row(date(2026, 9, 25), 1350.0)])
        r = fx_tool(2)
        assert r["trend"] == "상승"
        assert r["period_change_pct"] > 0

    def test_flat_trend_when_unchanged(self, monkeypatch):
        patch_db(monkeypatch, [row(date(2026, 9, 26), 1350.0), row(date(2026, 9, 25), 1350.0)])
        r = fx_tool(2)
        assert r["trend"] == "보합"
        assert r["period_change_pct"] == 0

    def test_single_row_has_no_period_change_pct(self, monkeypatch):
        patch_db(monkeypatch, [row(D0, 1350.0, None)])
        r = fx_tool(1)
        assert r["period_change_pct"] is None
        assert r["trend"] == "알수없음"
        assert r["daily_change_pct"] is None

    def test_no_data_is_not_found(self, monkeypatch):
        patch_db(monkeypatch, [])
        r = fx_tool(5)
        assert r == {
            "pair_name": "원/달러",
            "period_days": 5,
            "found": False,
            "message": "환율 데이터가 아직 수집되지 않았습니다.",
        }

    @pytest.mark.parametrize("raw, expected", [("abc", 5), (None, 5), ("3", 3), (0, 1), (9999, 60)])
    def test_period_days_is_sanitized(self, monkeypatch, raw, expected):
        patch_db(monkeypatch, [])
        assert fx_tool(raw)["period_days"] == expected


@pytest.mark.integration
class TestFxToolDevDB:
    def test_real_data_has_a_recent_rate(self, dev_db):
        from sqlalchemy import text

        if dev_db.execute(text("SELECT count(*) FROM exchange_rate")).scalar() == 0:
            pytest.skip("exchange_rate가 비어 있음: python -m scripts.collect_exchange_rate 먼저 실행")

        r = fx_tool(5)
        assert r["found"] is True
        assert r["pair_name"] == "원/달러"
        assert r["latest_rate"] > 0
        assert r["trend"] in {"상승", "하락", "보합", "알수없음"}
