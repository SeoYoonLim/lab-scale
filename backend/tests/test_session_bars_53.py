"""53차: 분봉 세션 분류(app/session_bars.py). 순수 함수, 네트워크·DB 없음.
사례는 보존 분봉(2026-11-05~11-13) 전수 조사에서 실제로 관찰한 모양을 줄여 만든 것이다."""
from datetime import date, datetime
from decimal import Decimal

import pytest

from app import krx_calendar, session_bars as sb

KST = krx_calendar.KST
D = date(2026, 11, 12)
DONE = datetime(2026, 11, 12, 21, 0, tzinfo=KST)


def row(hm, o, h=None, lo=None, c=None, v=100, day="20261112", sec="00"):
    h = o if h is None else h
    lo = o if lo is None else lo
    c = o if c is None else c
    return {"localDateTime": f"{day}{hm}{sec}", "openPrice": o, "highPrice": h, "lowPrice": lo, "currentPrice": c,
            "accumulatedTradingVolume": v}


def stock_day(auction_hm="1530", close=208000, extra=()):
    """삼성전자 11-12형: 정규장, 종가 단일가, 장후 종가매매, 애프터마켓. 원본처럼 시간 순서로 돌려준다(54차: 순서 검사)."""
    return sorted(_stock_day(auction_hm, close, extra), key=lambda r: r["localDateTime"])


def _stock_day(auction_hm, close, extra):
    return [row("0900", 208500, 209000, 208000, 208500, 1_000_000), row("1100", 212000, 214000, 211500, 213000, 9_000_000),
            row("1519", 207000, 207500, 206000, 207000, 3_000_000), row(auction_hm, close, v=5_004_149),
            row("1540", close, v=300), row("1559", close, v=84), row("1600", 208000, 209000, 207500, 208500, 300_000),
            row("1959", 206500, v=332_056), *extra]


def classify(rows, asset="stock", d=D, collected=DONE):
    by, probs, orphan = sb.parse_bars(sb.load_rows(__import__("json").dumps(rows).encode()))
    assert not orphan
    return sb.classify_day(d, by.get(d, []), asset, collected, probs.get(d))


def test_regular_day_has_four_sessions_with_correct_aggregates():
    r = classify(stock_day())
    assert r.status == "ok" and r.flags == [] and set(r.sessions) == set(sb.SESSIONS)
    reg, auc, pc, am = (r.sessions[s] for s in sb.SESSIONS)
    assert (reg.open, reg.high, reg.low, reg.close, reg.volume, reg.bar_count) == (208500, 214000, 206000, 207000, 13_000_000, 3)
    assert reg.first_bar_at == datetime(2026, 11, 12, 9, 0, tzinfo=KST) and reg.last_bar_at.hour == 15
    assert (auc.close, auc.volume, auc.bar_count, auc.first_bar_at.minute) == (208000, 5_004_149, 1, 30)
    assert (pc.close, pc.volume, pc.bar_count) == (208000, 384, 2)
    assert (am.open, am.high, am.low, am.close, am.volume) == (208000, 209000, 206500, 206500, 632_056)


def test_late_closing_auction_at_1532_is_auction_not_post_close():
    r = classify(stock_day(auction_hm="1532"))
    assert r.status == "ok" and r.flags == ["late_closing_auction"]
    assert r.sessions[sb.CLOSING_AUCTION].first_bar_at.minute == 32
    assert r.sessions[sb.POST_CLOSE].bar_count == 2  # 15:40 뒤 봉만


def test_late_auction_must_be_single_flat_bar():
    r = classify(stock_day(auction_hm="1532")[:3] + [row("1532", 208000, 208500, 208000, 208000)] + stock_day(auction_hm="1532")[4:])
    assert r.status == "rejected" and "1532" in r.unclassified
    r2 = classify(stock_day(auction_hm="1532", extra=[row("1535", 208000)]))
    assert r2.status == "rejected" and set(r2.unclassified) >= {"1532", "1535"}


def test_1530_and_1531_1539_together_is_rejected():
    r = classify(stock_day(extra=[row("1532", 208000)]))
    assert r.status == "rejected" and r.unclassified == ["1532"]


def test_non_flat_1530_bar_is_rejected():
    rows = stock_day()
    rows[3] = row("1530", 208000, 208500, 207500, 208000)
    assert classify(rows).status == "rejected"


def test_etf_without_closing_auction_keeps_last_trade_and_is_flagged():
    """ETF 385540형: 마지막 체결 15:16~15:19, 15:30 이후 봉 없음."""
    rows = [row("0902", 106000, v=10), row("1518", 106210, v=5)]
    r = classify(rows, asset="etf")
    assert r.status == "ok" and r.flags == ["no_closing_auction"] and set(r.sessions) == {sb.REGULAR}
    assert r.sessions[sb.REGULAR].close == 106210  # 종가 단일가 행을 만들지 않는다


def test_post_close_without_auction_is_rejected():
    r = classify([row("0902", 106000), row("1540", 106000)], asset="etf")
    assert r.status == "rejected" and r.unclassified == ["1540"]


def test_post_close_must_equal_auction_price():
    r = classify(stock_day(extra=[row("1545", 207000)]))
    assert r.status == "rejected" and r.unclassified == ["1545"]


def test_after_market_only_for_stocks():
    r = classify(stock_day(), asset="etf")
    assert r.status == "rejected" and {"1600", "1959"} <= set(r.unclassified)
    assert classify(stock_day(), asset=None).status == "rejected"  # 자산 구분을 모르면 16시 이후를 분류하지 않는다


def test_after_market_before_launch_date_is_rejected():
    d = date(2026, 9, 11)
    rows = [row(r["localDateTime"][8:12], r["openPrice"], r["highPrice"], r["lowPrice"], r["currentPrice"],
                r["accumulatedTradingVolume"], day="20260911") for r in stock_day()]
    r = classify(rows, d=d, collected=datetime(2026, 9, 11, 21, 0, tzinfo=KST))
    assert r.status == "rejected" and "1600" in r.unclassified


@pytest.mark.parametrize("hm, expect", [("1520", "ok"), ("1521", "rejected"), ("1529", "rejected"), ("0859", "rejected"),
                                        ("2000", "rejected")])
def test_session_boundaries(hm, expect):
    """15:20 봉 = 연속매매 마지막(KODEX 레버리지 11-10 관찰), 15:21~15:29·09:00 전·20:00 이후는 분류하지 않는다."""
    r = classify(stock_day(extra=[row(hm, 207100, v=20)]))
    assert r.status == expect
    if expect == "ok":
        assert r.sessions[sb.REGULAR].last_bar_at.minute == 20


@pytest.mark.parametrize("bad", [
    {"localDateTime": "2026111209000"}, {"localDateTime": "20261112x90000"}, {"localDateTime": "20261332090000"},
])
def test_bad_timestamps_cannot_be_placed(bad):
    rows = stock_day() + [{**row("0901", 1), **bad}]
    by, probs, orphan = sb.parse_bars(sb.load_rows(__import__("json").dumps(rows).encode()))
    assert orphan  # 날짜를 알 수 없는 봉 -> 적재 단계가 파일을 거부한다


def test_seconds_not_zero_rejects_day():
    r = classify(stock_day(extra=[row("1000", 208000, sec="30")]))
    assert r.status == "rejected" and "seconds_not_zero" in r.reasons


def test_duplicate_timestamp_rejects_day():
    r = classify(stock_day(extra=[row("1100", 212000, 214000, 211500, 213000, 9_000_000)]))
    assert r.status == "rejected" and "duplicate_timestamp" in r.reasons


@pytest.mark.parametrize("patch", [{"accumulatedTradingVolume": -8}, {"accumulatedTradingVolume": 1.5},
                                   {"openPrice": 0}, {"lowPrice": 999999}, {"highPrice": "1"}, {"currentPrice": True}])
def test_bad_values_reject_day(patch):
    """음수 거래량은 실제로 관찰됐다(284430, 11-06 -1·11-10 -8)."""
    rows = stock_day()
    rows[1] = {**rows[1], **patch}
    r = classify(rows)
    assert r.status == "rejected" and set(r.reasons) & {"bad_values", "ohlc_inconsistent"}


def test_missing_bars_inside_regular_are_normal_but_no_regular_is_rejected():
    r = classify([row("0900", 100), row("1300", 101)], asset="etf")
    assert r.status == "ok"  # 체결이 없는 분은 봉이 없다
    r2 = classify([row("1530", 100), row("1540", 100)], asset="etf")
    assert r2.status == "rejected" and "no_regular_bars" in r2.reasons


def test_non_trading_and_unknown_days_are_rejected():
    sun = date(2026, 11, 15)
    rows = [row("0900", 100, day="20261115")]
    assert "non_trading_day" in classify(rows, d=sun, collected=datetime(2026, 11, 16, tzinfo=KST)).reasons
    far = date(2027, 3, 3)
    assert "calendar_unknown" in classify([row("0900", 100, day="20270303")], d=far,
                                          collected=datetime(2027, 3, 4, tzinfo=KST)).reasons


def test_special_schedule_day_is_rejected():
    cal = krx_calendar.calendar()
    special = krx_calendar.Calendar(**{**cal.__dict__, "special_close": {**cal.special_close, D: __import__("datetime").time(16, 30)}})
    by, probs, _ = sb.parse_bars(sb.load_rows(__import__("json").dumps(stock_day()).encode()))
    r = sb.classify_day(D, by[D], "stock", DONE, probs.get(D), cal=special)
    assert r.status == "rejected" and r.reasons == ["schedule_unknown"]  # 54차: 달력 특수 마감 + 등록된 세션 일정 없음


@pytest.mark.parametrize("collected, reason", [(datetime(2026, 11, 12, 20, 9, tzinfo=KST), "incomplete_day"), (None, "collected_at_unknown")])
def test_incomplete_or_unknown_collection_time_is_rejected(collected, reason):
    r = classify(stock_day(), collected=collected)
    assert r.status == "rejected" and reason in r.reasons


def test_official_close_is_not_used_for_classification():
    """종가 단일가 판정은 시각·단일가 모양으로만 한다. 공식 종가와 다른 값이어도 분류는 같고, 대조는 적재 단계가 한다."""
    r = classify(stock_day(close=199000))
    assert r.status == "ok" and r.sessions[sb.CLOSING_AUCTION].close == 199000


def test_decimal_prices_are_preserved_exactly():
    """수정주가 ETF 분봉의 소수 12자리(관찰값)를 float 반올림 없이 유지한다."""
    data = b'[{"localDateTime":"20261110101800","currentPrice":17910.0,"openPrice":17929.949874686718,'            b'"highPrice":17929.949874686718,"lowPrice":17910.0,"accumulatedTradingVolume":8}]'
    by, probs, orphan = sb.parse_bars(sb.load_rows(data))
    d = date(2026, 11, 10)
    assert by[d][0].open == Decimal("17929.949874686718") and not probs and not orphan
    r = sb.classify_day(d, by[d], "etf", datetime(2026, 11, 14, tzinfo=KST))
    assert r.status == "ok" and r.sessions[sb.REGULAR].high == Decimal("17929.949874686718")
