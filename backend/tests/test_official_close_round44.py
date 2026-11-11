"""2026-11-11(44차): 공식 종가(KRX 정규장 종가) 복원과 원천 종가 차이 분류(app/official_close.py)."""
from datetime import date

import pytest

from app import official_close as oc


def _row(d, close, change, o=None, h=None, l=None, v=1000):
    c = f"{close:,}"
    return {"localTradedAt": d, "closePrice": c, "compareToPreviousClosePrice": f"{change:,}",
            "openPrice": f"{o or close:,}", "highPrice": f"{h or close:,}", "lowPrice": f"{l or close:,}",
            "accumulatedTradingVolume": v}


# 2026-11-11 16:34 네이버 일별 시세(삼성전자) 실제 값. 11-11 행은 애프터마켓 중이라 바뀌는 중이었다
SAMSUNG = [
    _row("2026-11-06", 193500, -8000),
    _row("2026-11-09", 187000, -6500, 187200, 195400, 183800, 19855892),
    _row("2026-11-10", 197300, 7400, 199400, 201500, 196400, 15289184),
    _row("2026-11-11", 205000, 6100),
]


def test_official_close_is_next_day_base_price():
    days = {d.day.isoformat(): d for d in oc.classify(SAMSUNG, "stock")}
    assert days["2026-11-10"].official_close == 198900 and days["2026-11-10"].kind == oc.AFTER_MARKET  # 기사·기준가와 같다
    assert days["2026-11-09"].official_close == 189900 and days["2026-11-09"].source_close == 187000
    assert days["2026-11-11"].official_close is None and days["2026-11-11"].kind == oc.PROVISIONAL


def test_before_after_market_start_differences_are_base_adjustments_not_after_market():
    rows = [_row("2026-09-10", 1000, 0), _row("2026-09-11", 990, -20)]  # 9-10 기준가 1,010 != 종가 1,000
    d = oc.classify(rows, "stock")[0]
    assert d.kind == oc.BASE_ADJUSTED and d.official_close == 1000  # 원천 종가 유지


def test_gap_beyond_price_limit_is_base_adjusted():
    rows = [_row("2026-10-01", 100000, 0), _row("2026-10-02", 20500, 500)]  # 기준가 20,000(분할 등)
    d = oc.classify(rows, "stock")[0]
    assert d.kind == oc.BASE_ADJUSTED and d.official_close == 100000


def test_etf_distribution_keeps_actual_close():
    rows = [_row("2026-10-28", 95650, 0), _row("2026-10-29", 94465, -1050)]  # 기준가 95,515(분배락)
    d = oc.classify(rows, "etf")[0]
    assert d.kind == oc.DISTRIBUTION and d.official_close == 95650


def test_official_records_skip_stock_latest_day_and_recompute_change():
    recs, stats = oc.official_records("005930", "stock", date(2026, 11, 9), rows=SAMSUNG)
    assert [r["price_date"].isoformat() for r in recs] == ["2026-11-09", "2026-11-10"]
    assert recs[1]["close_price"] == 198900 and recs[1]["change_pct"] == round((198900 / 189900 - 1) * 100, 2)
    assert (recs[1]["open_price"], recs[1]["high_price"], recs[1]["low_price"], recs[1]["volume"]) == (199400, 201500, 196400, 15289184)
    assert recs[1]["low_price"] <= recs[1]["close_price"] <= recs[1]["high_price"]
    assert stats["skipped_provisional"] == 1


def test_official_records_keep_etf_latest_day():
    rows = [_row("2026-11-10", 82000, 3510), _row("2026-11-11", 84540, 2540)]
    recs, _ = oc.official_records("069500", "etf", date(2026, 11, 10), rows=rows)
    assert [(r["price_date"].isoformat(), r["close_price"]) for r in recs] == [("2026-11-10", 82000), ("2026-11-11", 84540)]
    assert recs[1]["change_pct"] == round((84540 / 82000 - 1) * 100, 2)


def test_fetch_daily_pages_until_since_and_keeps_one_prior_row():
    pages = {1: [_row(f"2026-11-{d:02d}", 100, 0) for d in range(30, 0, -1)][:oc.PAGE_SIZE]}
    pages[1] = [_row(f"2026-{m:02d}-{d:02d}", 100, 0) for m, d in [(11, 11), (11, 10), (11, 9)]] * 20  # 60개(꽉 참)
    pages[2] = [_row("2026-11-06", 100, 0), _row("2026-11-05", 100, 0)]
    calls = []

    def get(url, params):
        calls.append(params["page"])
        return pages[params["page"]]

    rows = oc.fetch_daily("005930", date(2026, 11, 9), get=get)
    assert calls == [1, 2]
    assert [r["localTradedAt"] for r in rows] == ["2026-11-06", "2026-11-09", "2026-11-10", "2026-11-11"]


def test_fetch_daily_rejects_unexpected_payload():
    with pytest.raises(ValueError):
        oc.fetch_daily("005930", date(2026, 11, 9), get=lambda url, params: {"error": "x"})
