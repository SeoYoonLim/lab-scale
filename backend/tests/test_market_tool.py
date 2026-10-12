from datetime import date, timedelta

import pytest

import app.tools.market_tool as mt
from app.models import Company
from app.tools.market_tool import (
    IN_LINE_PP,
    compare_returns,
    index_code_for_market,
    is_index_current,
    market_data_is_current,
    market_tool,
)


def series(start: date, closes: list[float]) -> list[tuple[date, float]]:
    return [(start + timedelta(days=i), c) for i, c in enumerate(closes)]


D0 = date(2026, 9, 1)


class TestIndexCodeForMarket:
    @pytest.mark.parametrize(
        "market, code",
        [("KOSPI", "KS11"), ("KOSDAQ", "KQ11"), ("KOSDAQ GLOBAL", "KQ11"), ("kospi", "KS11"), ("KONEX", None), (None, None), ("", None)],
    )
    def test_mapping(self, market, code):
        assert index_code_for_market(market) == code


class TestCompareReturns:
    def test_outperform_uses_window_start_close_as_base(self):
        stock = series(D0, [100, 110, 121])  # 2거래일: 100 -> 121 = +21%
        index = series(D0, [1000, 1000, 1100])  # +10%
        r = compare_returns(stock, index, period_days=2)
        assert r["stock_change_pct"] == 21.0
        assert r["market_change_pct"] == 10.0
        assert r["diff_pct_point"] == 11.0
        assert r["relative"] == "outperform"
        assert (r["start_date"], r["end_date"], r["period_days"]) == ("2026-09-01", "2026-09-03", 2)

    def test_one_day_equals_daily_change(self):
        r = compare_returns(series(D0, [100, 102]), series(D0, [2000, 1980]), period_days=1)
        assert (r["stock_change_pct"], r["market_change_pct"]) == (2.0, -1.0)

    def test_underperform_when_both_fall(self):
        # 둘 다 하락해도 시장보다 덜 빠졌으면 outperform, 더 빠졌으면 underperform
        r = compare_returns(series(D0, [100, 90]), series(D0, [100, 97]), period_days=1)
        assert (r["stock_change_pct"], r["market_change_pct"]) == (-10.0, -3.0)
        assert r["diff_pct_point"] == -7.0
        assert r["relative"] == "underperform"

    def test_in_line_within_threshold(self):
        r = compare_returns(series(D0, [100, 101]), series(D0, [100, 100.6]), period_days=1)
        assert abs(r["diff_pct_point"]) < IN_LINE_PP
        assert r["relative"] == "in_line"

    def test_only_dates_present_in_both_are_used(self):
        stock = series(D0, [100, 105, 110, 120])  # 9/1..9/4
        index = [(D0, 1000.0), (D0 + timedelta(days=3), 1200.0)]  # 9/2, 9/3 지수 없음
        r = compare_returns(stock, index, period_days=3)
        assert (r["start_date"], r["end_date"], r["period_days"]) == ("2026-09-01", "2026-09-04", 1)
        assert (r["stock_change_pct"], r["market_change_pct"]) == (20.0, 20.0)

    def test_window_shorter_than_requested_reports_actual_days(self):
        r = compare_returns(series(D0, [100, 110, 121]), series(D0, [1, 1, 1]), period_days=30)
        assert r["period_days"] == 2

    def test_takes_latest_window_when_more_data_than_needed(self):
        r = compare_returns(series(D0, [50, 100, 200, 220]), series(D0, [1, 1, 1, 1]), period_days=1)
        assert r["start_date"] == "2026-09-03" and r["stock_change_pct"] == 10.0

    @pytest.mark.parametrize("stock, index", [([], []), (series(D0, [100]), series(D0, [100])), (series(D0, [100, 101]), [])])
    def test_insufficient_data_returns_none(self, stock, index):
        assert compare_returns(stock, index, period_days=1) is None


class _Q:
    """market_tool이 쓰는 db.query(...) 체인을 흉내낸다. 어떤 필터/정렬이 와도 미리 정한 결과를 돌려준다."""

    def __init__(self, rows):
        self._rows = rows

    def filter(self, *a, **k):
        return self

    order_by = limit = filter

    def all(self):
        return list(self._rows)

    def scalar(self):
        return self._rows[0] if self._rows else None


class MarketFakeDB:
    def __init__(self, companies, *, last_index_date, stock_rows, index_rows, latest_stock_date):
        self._companies = companies
        self._answers = {
            "index_max": [last_index_date] if last_index_date else [],
            "stock": stock_rows,
            "index": index_rows,
            "stock_max": [latest_stock_date] if latest_stock_date else [],
        }
        self._calls = 0

    def query(self, *entities):
        if entities and entities[0] is Company:
            return _Q(self._companies)
        # market_tool의 조회 순서: 지수 마지막 날짜 -> 종목 종가 -> 지수 종가 -> 종목 마지막 날짜
        key = ["index_max", "stock", "index", "stock_max"][self._calls]
        self._calls += 1
        return _Q(self._answers[key])

    def close(self):
        pass


def patch_db(monkeypatch, **kwargs):
    companies = kwargs.pop("companies", [Company(id=1, ticker="005930", name="삼성전자", market="KOSPI")])
    monkeypatch.setattr(mt, "SessionLocal", lambda: MarketFakeDB(companies, **kwargs))


def _rows(closes, start=D0):
    return [(d, c) for d, c in series(start, closes)]


class TestMarketToolShape:
    def test_found_result_shape(self, monkeypatch):
        stock = _rows([100, 110, 121])
        patch_db(
            monkeypatch,
            last_index_date=D0 + timedelta(days=2),
            stock_rows=list(reversed(stock)),  # market_tool은 최신순으로 받아 뒤집는다
            index_rows=_rows([1000, 1000, 1100]),
            latest_stock_date=D0 + timedelta(days=2),
        )
        r = market_tool("삼성전자", 2)
        assert r["found"] is True
        assert {
            "ticker", "company_name", "market", "index_code", "index_name", "period_days", "start_date", "end_date",
            "stock_change_pct", "market_change_pct", "diff_pct_point", "relative", "summary",
        } <= set(r)
        assert (r["market"], r["index_code"], r["index_name"]) == ("KOSPI", "KS11", "코스피")
        assert (r["stock_change_pct"], r["market_change_pct"], r["diff_pct_point"]) == (21.0, 10.0, 11.0)
        assert "note" not in r
        assert "11.00%p" in r["summary"]

    def test_note_when_index_data_ends_before_latest_stock_price(self, monkeypatch):
        stock = _rows([100, 110])
        patch_db(
            monkeypatch,
            last_index_date=D0 + timedelta(days=1),
            stock_rows=list(reversed(stock)),
            index_rows=_rows([1000, 1010]),
            latest_stock_date=D0 + timedelta(days=6),
        )
        r = market_tool("삼성전자", 1)
        assert r["found"] is True and "지수 데이터가" in r["note"]

    def test_kosdaq_global_uses_kosdaq_index(self, monkeypatch):
        stock = _rows([100, 110])
        patch_db(
            monkeypatch,
            companies=[Company(id=2, ticker="041510", name="에스엠", market="KOSDAQ GLOBAL")],
            last_index_date=D0 + timedelta(days=1),
            stock_rows=list(reversed(stock)),
            index_rows=_rows([800, 808]),
            latest_stock_date=D0 + timedelta(days=1),
        )
        r = market_tool("에스엠", 1)
        assert (r["index_code"], r["index_name"]) == ("KQ11", "코스닥")

    def test_unknown_company_is_not_found(self, monkeypatch):
        patch_db(monkeypatch, last_index_date=None, stock_rows=[], index_rows=[], latest_stock_date=None)
        r = market_tool("없는종목XYZ", 5)
        assert r["found"] is False and "찾지 못했습니다" in r["message"]

    def test_company_without_market_is_not_found(self, monkeypatch):
        patch_db(
            monkeypatch,
            companies=[Company(id=3, ticker="000001", name="시장미상", market=None)],
            last_index_date=None, stock_rows=[], index_rows=[], latest_stock_date=None,
        )
        r = market_tool("시장미상", 5)
        assert r["found"] is False and "상장 시장 정보가 없어" in r["message"]

    def test_no_index_data_is_not_found(self, monkeypatch):
        patch_db(monkeypatch, last_index_date=None, stock_rows=[], index_rows=[], latest_stock_date=None)
        r = market_tool("삼성전자", 5)
        assert r["found"] is False and "수집되지 않았습니다" in r["message"]

    def test_insufficient_stock_data_is_not_found(self, monkeypatch):
        patch_db(
            monkeypatch, last_index_date=D0, stock_rows=[(D0, 100)], index_rows=_rows([1000]), latest_stock_date=D0
        )
        r = market_tool("삼성전자", 5)
        assert r["found"] is False and "부족" in r["message"]

    @pytest.mark.parametrize("raw, expected", [("abc", 5), (None, 5), ("3", 3), (0, 1), (9999, 60)])
    def test_period_days_is_sanitized(self, monkeypatch, raw, expected):
        patch_db(monkeypatch, last_index_date=None, stock_rows=[], index_rows=[], latest_stock_date=None)
        assert market_tool("삼성전자", raw)["period_days"] == expected


@pytest.mark.integration
class TestMarketToolDevDB:
    def test_real_company_against_real_index_data(self, dev_db):
        from sqlalchemy import text

        if dev_db.execute(text("SELECT count(*) FROM market_index")).scalar() == 0:
            pytest.skip("market_index가 비어 있음: python -m scripts.collect_market_index 먼저 실행")

        r = market_tool("삼성전자", 5)
        assert r["found"] is True
        assert (r["market"], r["index_code"]) == ("KOSPI", "KS11")
        assert 1 <= r["period_days"] <= 5
        assert r["relative"] in {"outperform", "underperform", "in_line"}
        assert r["diff_pct_point"] == pytest.approx(r["stock_change_pct"] - r["market_change_pct"], abs=0.011)
        # 같은 구간을 SQL로 다시 계산해 tool 값과 맞는지 확인한다.
        s = dict(dev_db.execute(text(
            "SELECT s.price_date, s.close_price FROM stock_price s JOIN company c ON c.id = s.company_id "
            "WHERE c.name = '삼성전자' AND s.price_date IN (:a, :b)"), {"a": r["start_date"], "b": r["end_date"]}).all())
        m = dict(dev_db.execute(text(
            "SELECT price_date, close_price FROM market_index WHERE index_code = 'KS11' AND price_date IN (:a, :b)"),
            {"a": r["start_date"], "b": r["end_date"]}).all())
        start, end = date.fromisoformat(r["start_date"]), date.fromisoformat(r["end_date"])
        assert r["stock_change_pct"] == pytest.approx((float(s[end]) / float(s[start]) - 1) * 100, abs=0.011)
        assert r["market_change_pct"] == pytest.approx((float(m[end]) / float(m[start]) - 1) * 100, abs=0.011)


class TestCollect:
    def test_index_row_converts_change_to_percent_and_handles_nan(self):
        import pandas as pd

        from app.collectors import _index_row

        ts = pd.Timestamp("2026-09-17")
        ok = _index_row(ts, pd.Series({"Close": 6724.344, "Change": 0.0009}))
        assert ok == {"price_date": date(2026, 9, 17), "close_price": 6724.34, "change_pct": 0.09}
        nan = _index_row(ts, pd.Series({"Close": 100.0, "Change": float("nan")}))
        assert nan["change_pct"] is None
        assert _index_row(ts, pd.Series({"Close": 100.0}))["change_pct"] is None


class TestIsIndexCurrent:
    """코드가 market_tool을 스스로 덧붙여도 되는지: 지수가 종목의 최신 주가 날짜까지 있어야 한다."""

    @pytest.mark.parametrize(
        "stock, index, expected",
        [
            (date(2026, 10, 8), date(2026, 10, 8), True),
            (date(2026, 10, 8), date(2026, 10, 9), True),   # 지수가 더 최근이어도 된다
            (date(2026, 10, 8), date(2026, 10, 7), False),  # 하루만 뒤처져도 덧붙이지 않는다
            (date(2026, 10, 8), date(2026, 9, 17), False),  # 2026-10-11 dev DB 상태(지수 원천이 9/17에서 멈춤)
            (None, date(2026, 10, 8), False),
            (date(2026, 10, 8), None, False),
            (None, None, False),
        ],
    )
    def test_cases(self, stock, index, expected):
        assert is_index_current(stock, index) is expected


class _Scalar:
    def __init__(self, value):
        self.value = value

    def filter(self, *a, **k):
        return self

    def scalar(self):
        return self.value


class _DatesDB:
    """market_data_is_current가 하는 두 번의 max(price_date) 조회에 차례로(주가, 지수) 값을 돌려준다."""

    def __init__(self, stock_date, index_date):
        self.values = [stock_date, index_date]
        self.closed = False

    def query(self, *a, **k):
        return _Scalar(self.values.pop(0))

    def close(self):
        self.closed = True


class TestMarketDataIsCurrent:
    def _setup(self, monkeypatch, market, stock_date, index_date):
        company = None if market == "unknown" else Company(id=1, ticker="005930", name="삼성전자", market=market)
        db = _DatesDB(stock_date, index_date)
        monkeypatch.setattr(mt, "SessionLocal", lambda: db)
        monkeypatch.setattr(mt, "resolve_company", lambda db, ticker: type("R", (), {"company": company})())
        return db

    def test_current(self, monkeypatch):
        db = self._setup(monkeypatch, "KOSPI", date(2026, 10, 8), date(2026, 10, 8))
        assert market_data_is_current("삼성전자") is True
        assert db.closed

    def test_stale_index(self, monkeypatch):
        self._setup(monkeypatch, "KOSPI", date(2026, 10, 8), date(2026, 9, 17))
        assert market_data_is_current("삼성전자") is False

    def test_no_index_rows(self, monkeypatch):
        self._setup(monkeypatch, "KOSDAQ", date(2026, 10, 8), None)
        assert market_data_is_current("삼성전자") is False

    @pytest.mark.parametrize("market", ["KONEX", None, ""])
    def test_market_without_a_supported_index(self, monkeypatch, market):
        """코스피/코스닥이 아니면 market_tool이 비교를 지원하지 않는다. 날짜 조회까지 가지 않는다."""
        db = self._setup(monkeypatch, market, date(2026, 10, 8), date(2026, 10, 8))
        assert market_data_is_current("삼성전자") is False
        assert len(db.values) == 2 and db.closed

    def test_unknown_company(self, monkeypatch):
        db = self._setup(monkeypatch, "unknown", date(2026, 10, 8), date(2026, 10, 8))
        assert market_data_is_current("없는종목") is False
        assert db.closed


@pytest.mark.integration
class TestMarketDataIsCurrentDevDB:
    def test_matches_the_stored_dates(self, dev_db):
        """실제 dev DB: 저장된 주가/지수의 최신 날짜를 직접 조회한 것과 같은 판단이어야 한다."""
        from sqlalchemy import func

        from app.models import MarketIndex, StockPrice

        company = dev_db.query(Company).filter(Company.name == "삼성전자").one()
        latest_stock = dev_db.query(func.max(StockPrice.price_date)).filter(StockPrice.company_id == company.id).scalar()
        latest_index = dev_db.query(func.max(MarketIndex.price_date)).filter(MarketIndex.index_code == "KS11").scalar()
        assert market_data_is_current("삼성전자") is (latest_index is not None and latest_index >= latest_stock)
        assert market_data_is_current("005930") is market_data_is_current("삼성전자")
        assert market_data_is_current("세상에없는종목XYZ") is False
