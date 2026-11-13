"""50차: 분봉 세션 집계와 저장 일봉 대조(app/session_ohlcv.py, scripts/audit_session_ohlcv.py). 네트워크·DB 없이."""
from datetime import date, datetime

import pytest

from app import krx_calendar, session_ohlcv as so
from scripts import audit_session_ohlcv as audit

KST = krx_calendar.KST
D = date(2026, 11, 12)
LATER = datetime(2026, 11, 13, 9, 0, tzinfo=KST)


def bar(hhmm: str, o, h, lo, c, v, d: str = "20261112") -> dict:
    return {"localDateTime": f"{d}{hhmm}00", "openPrice": o, "highPrice": h, "lowPrice": lo, "currentPrice": c,
            "accumulatedTradingVolume": v}


def samsung_like() -> list[dict]:
    """삼성전자 2026-11-12 관찰값의 축약: 정규장 208,500~214,000/206,000, 15:30 종가 208,000, 애프터마켓 206,500 마감."""
    return [
        bar("0900", 208500, 209000, 208000, 208500, 1_000_000),
        bar("1100", 212000, 214000, 211500, 213000, 9_000_000),
        bar("1400", 207000, 207500, 206000, 207000, 3_867_664),
        bar("1530", 208000, 208000, 208000, 208000, 5_004_149),
        bar("1540", 208000, 208000, 208000, 208000, 384),
        bar("1600", 208000, 209000, 207500, 208500, 300_000),
        bar("1959", 206500, 206500, 206500, 206500, 332_056),
    ]


STORED = {"open": 208500.0, "high": 214000.0, "low": 206000.0, "close": 208000.0, "volume": 19_721_557}


def test_aggregate_splits_sessions():
    s = so.aggregate(samsung_like())[D]
    assert set(s) == {so.REGULAR, so.CLOSING, so.AFTER}
    reg = s[so.REGULAR]
    assert (reg.open, reg.high, reg.low, reg.close, reg.first, reg.last) == (208500, 214000, 206000, 208000, "0900", "1530")
    assert reg.volume == 18_871_813
    assert s[so.CLOSING].volume == 384 and s[so.AFTER].close == 206500 and s[so.AFTER].volume == 632_056


def test_compare_day_regular_open_close_and_unattributed_volume():
    r = so.compare_day(STORED, so.aggregate(samsung_like())[D], D, LATER)
    assert r["issue"] is None and r["open_is_regular"] and r["close_is_regular_close"]
    assert r["high_from"] == so.REGULAR and r["low_from"] == so.REGULAR
    assert r["volume_regular"] == 18_871_813 and r["volume_after_market"] == 632_056
    # 일봉 거래량 - 분봉 합계(분봉에 없는 체결, 구성 미확인)
    assert r["volume_unattributed"] == 19_721_557 - 19_504_253
    assert r["volume_over_regular_pct"] == pytest.approx(4.5, abs=0.01)


def test_after_market_low_is_attributed_to_after_market():
    # 펩트론 2026-10-07 같은 경우: 정규장 저가 128,000, 애프터마켓 급락 91,500이 일봉 저가가 된다
    bars = [bar("0900", 130800, 136500, 128000, 131000, 500_000), bar("1530", 130600, 130600, 130600, 130600, 100_000),
            bar("1600", 129000, 129000, 120000, 121000, 200_000), bar("1959", 97000, 97000, 91500, 96900, 351_621)]
    stored = {"open": 130800.0, "high": 136500.0, "low": 91500.0, "close": 130600.0, "volume": 1_151_621}
    r = so.compare_day(stored, so.aggregate(bars)[D], D, LATER)
    assert r["low_from"] == so.AFTER and r["high_from"] == so.REGULAR and r["close_is_regular_close"]
    assert r["volume_after_market"] == 551_621 and r["volume_unattributed"] == 0


def test_missing_close_auction_bar_is_an_issue_not_a_guess():
    bars = [b for b in samsung_like() if b["localDateTime"][8:12] != "1530"]
    r = so.compare_day(STORED, so.aggregate(bars)[D], D, LATER)
    assert r["issue"] == "no_close_auction_bar" and "open_is_regular" not in r


def test_today_before_after_market_end_is_incomplete():
    assert so.day_complete(D, datetime(2026, 11, 12, 19, 59, tzinfo=KST)) is False
    assert so.day_complete(D, datetime(2026, 11, 12, 20, 10, tzinfo=KST)) is True
    r = so.compare_day(STORED, so.aggregate(samsung_like())[D], D, datetime(2026, 11, 12, 16, 0, tzinfo=KST))
    assert r["issue"] == "day_incomplete"


def test_no_bars_for_day():
    assert so.compare_day(STORED, {}, D, LATER)["issue"] == "no_regular_bars"


def test_etf_adjusted_minute_floats_match_rounded_daily():
    # 분배락 전 ETF 분봉은 수정주가 소수(498400 2026-11-05 15993.2345…), FDR 일봉은 정수 반올림
    bars = [bar("0900", 15993.234514, 15993.234514, 15245.426456, 15300.0, 100, d="20261105"),
            bar("1530", 15379.0, 15379.0, 15379.0, 15379.0, 50, d="20261105")]
    d = date(2026, 11, 5)
    stored = {"open": 15993.0, "high": 15993.0, "low": 15245.0, "close": 15379.0, "volume": 150}
    r = so.compare_day(stored, so.aggregate(bars)[d], d, LATER)
    assert r["open_is_regular"] and r["close_is_regular_close"] and r["high_from"] == so.REGULAR
    assert r["after_market"] is None and r["volume_after_market"] == 0


def test_fetch_minute_rejects_bad_payload_and_passes_range():
    seen = []
    got = so.fetch_minute("005930", date(2026, 11, 5), D, get=lambda u, p: seen.append(p) or [])
    assert got == [] and seen[0] == {"startDateTime": "202611050000", "endDateTime": "202611122359"}
    with pytest.raises(ValueError):
        so.fetch_minute("005930", D, D, get=lambda u, p: {"code": "x"})


def test_default_days_skips_incomplete_today_and_weekend():
    days = audit.default_days(datetime(2026, 11, 13, 15, 0, tzinfo=KST))  # 금요일 장중: 오늘은 미완성
    assert days[-1] == date(2026, 11, 12) and len(days) == 6 and days[0] == date(2026, 11, 5)
    assert audit.default_days(datetime(2026, 11, 14, 10, 0, tzinfo=KST))[-1] == date(2026, 11, 13)


def test_summarize_counts():
    rows = [
        {"asset_type": "stock", "issue": None, "open_is_regular": True, "close_is_regular_close": True,
         "high_from": "regular", "low_from": "after_market", "volume_over_regular_pct": 3.0},
        {"asset_type": "stock", "issue": "no_close_auction_bar"},
        {"asset_type": "etf", "issue": None, "open_is_regular": True, "close_is_regular_close": True,
         "high_from": "regular", "low_from": "regular", "volume_over_regular_pct": 0.0},
    ]
    s = audit.summarize(rows)
    assert s["stock"]["checked"] == 1 and s["stock"]["issues"] == {"no_close_auction_bar": 1}
    assert s["stock"]["low_from"] == {"after_market": 1} and s["etf"]["volume_over_regular_pct"]["max"] == 0.0


# ---------- 답 문구의 데이터 기준(app/price_basis.py) ----------

def test_after_market_included_only_for_stock_after_start():
    from app import price_basis as pb

    assert pb.after_market_included("stock", "2026-09-14") and pb.after_market_included("stock", date(2026, 11, 12))
    assert not pb.after_market_included("stock", "2026-09-11")
    assert not pb.after_market_included("etf", "2026-11-12") and not pb.after_market_included(None, "2026-11-12")
    assert not pb.after_market_included("stock", "not-a-date")


def test_day_text_notes_after_market_high_low_only_when_it_applies():
    from app.period_answers import day_text

    row = {"price_date": "2026-10-07", "close_price": 130600.0, "change_pct": 1.0, "volume": 1151621,
           "open_price": 130800.0, "high_price": 136500.0, "low_price": 91500.0}
    q = "펩트론 10월 7일 고가 저가 알려줘"
    assert "애프터마켓" in day_text("펩트론", row, q, "stock")
    assert "애프터마켓" not in day_text("펩트론", row, q, "etf")
    assert "애프터마켓" not in day_text("펩트론", {**row, "price_date": "2026-09-11"}, q, "stock")
    assert "애프터마켓" not in day_text("펩트론", row, "펩트론 10월 7일 종가", "stock")  # 고가·저가를 말하지 않으면 붙이지 않는다
    assert "애프터마켓" not in day_text("펩트론", row, q)  # 자산 구분을 모르면 단정하지 않는다


def test_outlook_volume_meaning_states_basis():
    from app.outlook import _SIGNAL_MEANING

    assert "애프터마켓" in _SIGNAL_MEANING["volume"] and "NXT" in _SIGNAL_MEANING["volume"]
