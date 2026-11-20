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


# ---------- 45차(2026-11-11): 개발 DB 적용 전 점검에서 찾은 두 가지 ----------
def test_fetch_daily_reads_full_etf_history_beyond_600_rows():
    """ETF 전체 기간(2023-10-30~, 약 750거래일)은 60행 x 10페이지(600행)를 넘는다. 전에는 10페이지에서 멈춰 2024-05 이전 행이
    수정주가로 남았다(조정 상태 혼재)."""
    from datetime import timedelta

    days = [date(2026, 11, 11) - timedelta(days=i) for i in range(800)]
    allrows = [_row(d.isoformat(), 100, 0) for d in days]  # 최신부터

    def get(url, params):
        p = params["page"]
        return allrows[(p - 1) * oc.PAGE_SIZE: p * oc.PAGE_SIZE]

    rows = oc.fetch_daily("069500", date(2024, 10, 1), get=get)  # 가짜 원천은 2024-09-03까지(800일), 필요한 행은 약 770개
    assert len(rows) > oc.PAGE_SIZE * 10  # 예전 한도(600행)를 넘는다
    assert rows[0]["localTradedAt"] == "2024-09-30" and rows[-1]["localTradedAt"] == "2026-11-11"  # since 전날 1행 포함


def test_fetch_daily_raises_when_source_does_not_reach_since():
    def get(url, params):  # 항상 꽉 찬 페이지만 준다(끝이 없다)
        return [_row("2026-11-11", 100, 0)] * oc.PAGE_SIZE

    with pytest.raises(ValueError, match="reach"):
        oc.fetch_daily("069500", date(2020, 1, 1), get=get)


def test_etf_latest_row_is_not_written_during_the_session():
    from datetime import datetime

    rows = [_row("2026-11-10", 82000, 3510), _row("2026-11-11", 84000, 2000)]
    intraday, stats = oc.official_records("069500", "etf", date(2026, 11, 10), rows=rows, now=datetime(2026, 11, 11, 11, 0))
    assert [r["price_date"].isoformat() for r in intraday] == ["2026-11-10"] and stats["skipped_provisional"] == 1
    after_close, _ = oc.official_records("069500", "etf", date(2026, 11, 10), rows=rows, now=datetime(2026, 11, 11, 15, 31))
    assert [r["price_date"].isoformat() for r in after_close] == ["2026-11-10", "2026-11-11"]


# ---------- 45차: 공식 종가를 원천(FDR) 수정주가 체계에 맞춰 쓴다(연속성 유지) ----------
def _src(d, close, o=None, h=None, l=None, v=10):
    return {"price_date": date.fromisoformat(d), "open_price": float(o or close), "high_price": float(h or close),
            "low_price": float(l or close), "close_price": float(close), "volume": v, "change_pct": None}


def test_with_source_uses_adjusted_ratio_so_corporate_actions_do_not_jump():
    """엘앤씨바이오(290650)처럼 원천(FDR)이 기업 이벤트로 과거를 0.5025배 조정한 종목: 네이버 실제 종가 46,000을 그대로 쓰면
    직전(FDR 23,843) 대비 +93% 가짜 급등이 생긴다. 공식 종가 x (FDR / 실제)로 써야 이어진다."""
    rows = [_row("2026-09-11", 47450, 0), _row("2026-09-14", 46000, -1450), _row("2026-09-15", 47150, 1150)]
    source = {date(2026, 9, 11): _src("2026-09-11", 23843), date(2026, 9, 14): _src("2026-09-14", 23114, 23400, 23500, 23000),
              date(2026, 9, 15): _src("2026-09-15", 23692)}
    recs, stats = oc.official_records("290650", "stock", date(2026, 9, 14), rows=rows, source=source)
    assert [r["close_price"] for r in recs] == [23114.0]  # 9-14 공식 = 실제 종가(같음) -> FDR 값 그대로, 9-15는 최신일이라 미저장
    assert recs[0]["change_pct"] == round((23114 / 23843 - 1) * 100, 2) and recs[0]["open_price"] == 23400.0


def test_with_source_after_market_day_is_official_times_ratio():
    rows = [_row("2026-11-09", 187000, -6500), _row("2026-11-10", 197300, 7400), _row("2026-11-11", 205000, 6100)]
    source = {date(2026, 11, 9): _src("2026-11-09", 187000), date(2026, 11, 10): _src("2026-11-10", 197300, 199400, 201500, 196400, 15289184)}
    recs, stats = oc.official_records("005930", "stock", date(2026, 11, 10), rows=rows, source=source)
    assert [(r["price_date"].isoformat(), r["close_price"]) for r in recs] == [("2026-11-10", 198900.0)]
    assert recs[0]["change_pct"] == round((198900 / 189900 - 1) * 100, 2)  # 전일(11-09)도 공식 종가 189,900 기준
    assert stats["after_market_dates"] == ["2026-11-09", "2026-11-10"]


def test_with_source_etf_keeps_source_adjusted_close():
    """ETF: 공식 종가 = 실제 종가이므로 x (FDR / 실제) = FDR 값. 분배 조정 체계가 그대로 유지된다(실제 종가로 덮어쓰지 않는다)."""
    rows = [_row("2026-09-11", 109500, 0), _row("2026-09-14", 105410, -4090), _row("2026-09-15", 106000, 590)]
    source = {date(2026, 9, 11): _src("2026-09-11", 109345), date(2026, 9, 14): _src("2026-09-14", 105261),
              date(2026, 9, 15): _src("2026-09-15", 105850)}
    recs, _ = oc.official_records("069500", "etf", date(2026, 9, 14), rows=rows, source=source, now=None)
    assert [r["close_price"] for r in recs] == [105261.0, 105850.0]


def test_with_source_missing_day_is_skipped_not_mixed():
    rows = [_row("2026-11-09", 187000, 0), _row("2026-11-10", 197300, 7400), _row("2026-11-11", 205000, 6100)]
    recs, stats = oc.official_records("005930", "stock", date(2026, 11, 10), rows=rows, source={date(2026, 11, 9): _src("2026-11-09", 187000)})
    assert recs == [] and stats["no_source"] == 1


# ---------- 46차: 원천 조정 비율이 다음 날 바뀐 애프터마켓 분류일(기업 이벤트 경계)은 교정하지 않는다 ----------
def test_event_boundary_inside_after_market_band_keeps_source():
    """삼성바이오로직스(207940) 2026-11-12 조회 실제 값. 10-02에 원천(FDR)이 과거를 0.9923배 조정했다(기준가 조정).
    10-01 실제 종가 1,428,000과 10-02 기준가 1,418,000의 차이(0.7%)가 제한폭 안이라 애프터마켓으로 보이지만, 기준가가 조정된 값이므로
    공식 종가 x 비율(1,407,083)은 조정을 두 번 적용한 값이다. 원천 값(1,417,007)을 그대로 둔다."""
    rows = [_row("2026-09-30", 1394000, 9000), _row("2026-10-01", 1428000, 37000), _row("2026-10-02", 1366000, -52000),
            _row("2026-10-06", 1318000, -36000), _row("2026-10-07", 1320000, 2000)]
    source = {date(2026, 9, 30): _src("2026-09-30", 1383268), date(2026, 10, 1): _src("2026-10-01", 1417007),
              date(2026, 10, 2): _src("2026-10-02", 1366000), date(2026, 10, 6): _src("2026-10-06", 1318000)}
    recs, stats = oc.official_records("207940", "stock", date(2026, 10, 1), rows=rows, source=source)
    by = {r["price_date"].isoformat(): r["close_price"] for r in recs}
    assert by["2026-10-01"] == 1417007.0  # 원천 그대로(교정하지 않음)
    assert stats.get("event_boundary") == 1 and "2026-10-01" not in stats["after_market_dates"]
    assert stats["event_boundary_dates"] == ["2026-10-01"]  # 승인 조건: 교정하지 않은 행을 따로 남긴다
    # 비율이 같은 다른 날은 그대로 교정된다(10-02: 10-06 기준가 1,318,000 + 36,000 = 1,354,000 vs 실제 1,366,000)
    assert by["2026-10-02"] == 1354000.0 and "2026-10-02" in stats["after_market_dates"]


def test_event_boundary_uses_one_for_last_row_so_intraday_noise_does_not_block_correction():
    """마지막 행(오늘 장중)은 FDR과 네이버를 다른 시각에 읽어 비율이 흔들린다(2026-11-12 주성엔지니어링 1.0 vs 0.9966).
    그 앞날(완료된 날, 비율 1)의 애프터마켓 교정은 막지 않는다."""
    rows = [_row("2026-11-10", 50000, 0), _row("2026-11-11", 51000, 1000), _row("2026-11-12", 52000, 1500)]
    source = {date(2026, 11, 10): _src("2026-11-10", 50000), date(2026, 11, 11): _src("2026-11-11", 51000),
              date(2026, 11, 12): _src("2026-11-12", 51800)}  # 장중이라 FDR 값이 네이버와 다르다
    recs, stats = oc.official_records("036930", "stock", date(2026, 11, 11), rows=rows, source=source)
    assert [(r["price_date"].isoformat(), r["close_price"]) for r in recs] == [("2026-11-11", 50500.0)]
    assert "event_boundary" not in stats


def test_event_boundary_is_not_triggered_by_source_rounding():
    """수정주가 구간(비율 0.5025)에서 FDR 원 단위 반올림만으로는 경계로 보지 않는다."""
    days = oc.classify([_row("2026-10-12", 64700, 0), _row("2026-10-13", 56700, -200), _row("2026-10-14", 57000, 300)], "stock")
    src = {date(2026, 10, 12): 32512.0, date(2026, 10, 13): 28491.0}  # 64,700 x 0.50250 = 32,511.75, 56,700 x 0.50250 = 28,491.75
    out = oc.mark_event_boundaries(days, src)
    assert out[0].kind == oc.AFTER_MARKET


def test_collect_prices_report_lists_held_rows(monkeypatch, tmp_path):
    """46차 승인 조건: 교정하지 않고 원천 값을 둔 행(이벤트 경계)을 수집 보고서에 따로 남긴다."""
    import json
    import sys

    from app.collectors import CollectResult
    from scripts import collect_prices

    (tmp_path / "u.json").write_text(json.dumps({"universe": [{"rank": 1, "ticker": "207940", "name": "삼성바이오로직스"},
                                                              {"rank": 2, "ticker": "005930", "name": "삼성전자"}]}), encoding="utf-8")
    monkeypatch.setattr(collect_prices, "UNIVERSE_DIR", tmp_path)
    monkeypatch.setattr(collect_prices, "_register", lambda *a, **k: None)
    monkeypatch.setattr(collect_prices.time, "sleep", lambda s: None)
    held = {"207940": ["2026-10-01"], "005930": []}
    monkeypatch.setattr(collect_prices, "fetch_and_save_stock",
                        lambda t, *a, **k: CollectResult(ok=True, saved=0, fetched=1, held_dates=held[t]))
    out = tmp_path / "r.json"
    monkeypatch.setattr(sys, "argv", ["collect_prices", "--universe", "u.json", "--close-basis", "official", "--report", str(out)])
    collect_prices.main()
    rep = json.loads(out.read_text(encoding="utf-8"))
    assert rep["summary"]["held_rows"] == [["207940", "2026-10-01"]]
    assert [r["held_dates"] for r in rep["rows"]] == [["2026-10-01"], []]



# ---------- 47차: source 모드 보호(교정된 공식 종가를 애프터마켓 종료가로 덮어쓰지 않는다) ----------
def test_source_guard_blocks_stock_rows_after_after_market_start():
    from app.collectors import source_after_market_guard

    recs = [{"price_date": date(2026, 9, 11)}, {"price_date": date(2026, 9, 14)}, {"price_date": date(2026, 11, 3)}]
    keep, blocked = source_after_market_guard(recs, "stock", allow=False)
    assert [r["price_date"] for r in keep] == [date(2026, 9, 11)] and blocked == [date(2026, 9, 14), date(2026, 11, 3)]
    assert source_after_market_guard(recs, "etf", allow=False) == (recs, [])  # ETF는 애프터마켓 대상이 아니다
    assert source_after_market_guard(recs, "stock", allow=True) == (recs, [])  # 명시 옵션으로만 푼다


def test_fetch_and_save_stock_defaults_to_official_and_uses_stored_asset_type(monkeypatch):
    import inspect

    from app import collectors

    assert inspect.signature(collectors.fetch_and_save_stock).parameters["close_basis"].default == "official"
    seen = {}

    def fake_records(ticker, asset_type, start, now=None, source=None, rows=None):
        seen["asset_type"] = asset_type
        raise RuntimeError("stop")  # 분류에 쓰는 자산 구분만 확인하고 멈춘다(네트워크·DB 쓰기 없음)

    monkeypatch.setattr(collectors, "_stored_asset_type", lambda t: "etf")
    monkeypatch.setattr(collectors, "_read_fdr", lambda *a, **k: None)
    monkeypatch.setattr("app.official_close.official_records", fake_records)
    r = collectors.fetch_and_save_stock("069500", "KODEX 200", start_date="2026-11-02", end_date="2026-11-05")
    assert not r.ok and seen["asset_type"] == "etf"  # 인자를 주지 않아도(backfill_history) DB 등록값 etf로 분류한다
