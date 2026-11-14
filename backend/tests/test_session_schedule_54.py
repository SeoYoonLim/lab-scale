"""54차: 거래일별 세션 일정(app/session_schedule.py)과 일정 기반 분류·집계(app/session_bars.py). 순수 함수, 네트워크·DB 없음.

2026-11-19(수능일) 사례는 **합성 분봉**이다. 실제 11-19 분봉은 아직 없다(시간 분류·집계 로직 시험용일 뿐, 실제 거래일 검증이 아니다).
"""
import copy
import json
from datetime import date, datetime, time
from decimal import Decimal

import pytest

from app import krx_calendar, session_bars as sb, session_schedule as ss

KST = krx_calendar.KST
SAT = date(2026, 11, 19)  # 수능일(합성)
NORMAL = date(2026, 11, 12)
RAW = json.loads(ss.PATH.read_text(encoding="utf-8"))


def row(hm, o, h=None, lo=None, c=None, v=100, day="20261119"):
    return {"localDateTime": f"{day}{hm}00", "openPrice": o, "highPrice": o if h is None else h, "lowPrice": o if lo is None else lo,
            "currentPrice": o if c is None else c, "accumulatedTradingVolume": v}


def classify(rows, asset="stock", d=SAT, collected=None, **kw):
    rows = sorted(rows, key=lambda r: r["localDateTime"])
    by, probs, orphan = sb.parse_bars(sb.load_rows(json.dumps(rows).encode()))
    assert not orphan
    collected = collected or datetime.combine(d, time(21, 0), tzinfo=KST)
    return sb.classify_day(d, by.get(d, []), asset, collected, probs.get(d), **kw)


def csat_stock_day(extra=()):
    """합성: 수능일 주식 하루(10:00 개장, 16:20 마지막 연속매매, 16:30 종가 단일가, 16:40~16:59 장후 종가매매, 17:00~19:59 애프터마켓)."""
    return [row("1000", 50000, 50500, 49800, 50200, 1000), row("1300", 50200, 51000, 50100, 50900, 500),
            row("1620", 50800, 50900, 50700, 50750, 300), row("1630", 50700, v=4000), row("1640", 50700, v=20),
            row("1659", 50700, v=5), row("1700", 50650, 50800, 50600, 50700, 60), row("1959", 50500, v=7), *extra]


# ---------- 일정 파일·조회 ----------

def test_default_schedule_matches_round53_rules():
    ds, why = ss.resolve(NORMAL)
    assert why is None and ds.kind == "default" and ds.status == "data_verified"
    assert (ds.regular.start, ds.regular.end, ds.closing_at, ds.late_window.start, ds.late_window.end) == ("0900", "1520", "1530", "1531", "1539")
    assert (ds.post_close.start, ds.post_close.end, ds.after_market.start, ds.after_market.end) == ("1540", "1559", "1600", "1959")
    assert ds.after_market_for("stock") is not None and ds.after_market_for("etf") is None and ds.after_market_for(None) is None
    assert ds.complete_after() == datetime(2026, 11, 12, 20, 10, tzinfo=KST)
    assert ds.regular_close == time(15, 30)


def test_csat_override_is_registered_with_sources_and_announced_status():
    ds, why = ss.resolve(SAT)
    assert why is None and ds.kind == "override" and ds.status == "announced"
    assert (ds.regular.start, ds.regular.end, ds.closing_at) == ("1000", "1620", "1630")
    assert (ds.post_close.start, ds.post_close.end, ds.after_market.start, ds.after_market.end) == ("1640", "1659", "1700", "1959")
    assert ds.late_window is None  # 관찰되지 않은 지연 체결 창은 열지 않는다
    assert ds.after_market_for("etf") is None  # ETF·ETN은 애프터마켓 대상 아님
    assert ds.complete_after() == datetime(2026, 11, 19, 20, 10, tzinfo=KST)
    assert any("yna.co.kr" in s for s in ds.sources) and all(s.startswith("https://") for s in ds.sources)


def test_only_csat_day_is_overridden_and_neighbors_use_default():
    for d in (date(2026, 11, 18), date(2026, 11, 20), date(2026, 11, 26)):
        assert ss.resolve(d)[0].kind == "default"
    assert list(ss.schedule().overrides) == [SAT]


@pytest.mark.parametrize("d, why", [(date(2026, 11, 15), "non_trading_day"), (date(2026, 12, 25), "non_trading_day"),
                                    (date(2027, 3, 3), "calendar_unknown")])
def test_unknown_or_closed_days_have_no_schedule(d, why):
    assert ss.resolve(d) == (None, why)


def test_calendar_special_close_without_registered_schedule_is_unknown():
    cal = krx_calendar.calendar()
    special = krx_calendar.Calendar(**{**cal.__dict__, "special_close": {**cal.special_close, NORMAL: time(14, 30)}})
    assert ss.resolve(NORMAL, cal=special) == (None, "schedule_unknown")


def test_calendar_mismatch_is_reported_for_csat_day():
    """세션 일정은 16:30 마감인데 기존 달력(krx_calendar.json)은 11-19 특수 마감이 없다(15:30). 54차에서는 달력을 바꾸지 않았다."""
    assert ss.calendar_mismatches() == [{"date": "2026-11-19", "schedule_close": "16:30", "calendar_close": "15:30"}]
    cal = krx_calendar.calendar()
    fixed = krx_calendar.Calendar(**{**cal.__dict__, "special_close": {**cal.special_close, SAT: time(16, 30)}})
    assert ss.calendar_mismatches(cal=fixed) == []


def _bad(mutate):
    data = copy.deepcopy(RAW)
    mutate(data)
    with pytest.raises(ValueError):
        ss.parse(data)


@pytest.mark.parametrize("mutate", [
    lambda d: d["overrides"][0].update(sources=[]),                                  # 근거 없음
    lambda d: d["overrides"][0].update(sources=["http://example.com"]),              # https 아님
    lambda d: d["overrides"][0].update(reason=""),                                   # 사유 없음
    lambda d: d["overrides"][0].update(status="guessed"),                            # 알 수 없는 상태
    lambda d: d["overrides"][0].update(date="2026-11-21"),                           # 토요일
    lambda d: d["overrides"][0].update(date="2026-12-25"),                           # 휴장일
    lambda d: d["overrides"][0].update(date="2027-01-04"),                           # 달력 범위 밖
    lambda d: d["overrides"].append(copy.deepcopy(d["overrides"][0])),               # 같은 날짜 두 번
    lambda d: d["overrides"][0]["regular"].update(end="16:35"),                      # 정규장이 종가 단일가와 겹침
    lambda d: d["overrides"][0]["post_close"].update(start="16:59", end="16:40"),    # 시작 > 끝
    lambda d: d["overrides"][0]["after_market"].update(start="16:50"),               # 장후 종가매매와 겹침
    lambda d: d["overrides"][0]["after_market"].update(asset_types=["stock", "etn"]),  # 알 수 없는 자산
    lambda d: d["default"]["regular"].update(start="9:0x"),                          # 시각 형식
    lambda d: d.update(market="NXT"),                                                # NXT 일정 금지
])
def test_invalid_schedule_files_are_rejected(mutate):
    _bad(mutate)


def test_new_special_day_only_needs_a_data_entry():
    """향후 임시 일정은 날짜·시각·대상·사유·근거만 더하면 적용된다(코드 변경 없음)."""
    data = copy.deepcopy(RAW)
    data["overrides"].append({"date": "2026-12-01", "label": "가상의 임시 일정", "status": "announced", "reason": "시험",
                              "sources": ["https://example.org/notice"], "regular": {"start": "09:30", "end": "15:50"},
                              "closing_auction": {"at": "16:00", "late_window": None}, "post_close": None, "after_market": None})
    sched = ss.parse(data)
    ds, _ = ss.resolve(date(2026, 12, 1), sched=sched)
    assert (ds.regular.start, ds.closing_at, ds.post_close, ds.after_market) == ("0930", "1600", None, None)
    rows = [row("0930", 100, day="20261201"), row("1550", 101, day="20261201"), row("1600", 101, day="20261201")]
    r = classify(rows, d=date(2026, 12, 1), sched_data=sched)
    assert r.status == "ok" and set(r.sessions) == {sb.REGULAR, sb.CLOSING_AUCTION}
    r2 = classify(rows + [row("1640", 101, day="20261201")], d=date(2026, 12, 1), sched_data=sched)  # 장후 창이 없는 날의 16:40
    assert r2.status == "rejected"


# ---------- 수능일 합성 분봉 ----------

def test_csat_synthetic_day_classifies_into_shifted_sessions():
    r = classify(csat_stock_day())
    assert r.status == "ok", r.reasons
    reg, auc, pc, am = (r.sessions[s] for s in sb.SESSIONS)
    assert reg.first_bar_at.hour == 10 and reg.first_bar_at.minute == 0  # 10:00 = 정규장 첫 봉
    assert (reg.last_bar_at.hour, reg.last_bar_at.minute) == (16, 20)  # 16:20 = 정규장 마지막 구간
    assert (auc.first_bar_at.hour, auc.first_bar_at.minute, auc.volume) == (16, 30, 4000)  # 16:30 = 종가 단일가
    assert (pc.first_bar_at.minute, pc.last_bar_at.minute, pc.bar_count, pc.volume) == (40, 59, 2, 25)  # 16:40~16:59
    assert (am.first_bar_at.hour, am.last_bar_at.hour, am.volume) == (17, 19, 67)  # 17:00~19:59
    assert r.schedule == {"date": "2026-11-19", "kind": "override", "status": "announced", "label": ss.resolve(SAT)[0].label}
    assert "calendar_close_mismatch" in r.flags


@pytest.mark.parametrize("hm", ["0900", "0930", "0959"])
def test_csat_bars_before_10_are_not_regular(hm):
    r = classify(csat_stock_day([row(hm, 50000)]))
    assert r.status == "rejected" and hm in r.unclassified


@pytest.mark.parametrize("hm, expect", [("1600", "regular"), ("1615", "regular"), ("1621", None), ("1629", None),
                                        ("1631", None), ("1635", None), ("1639", None)])
def test_csat_1600_1639_are_never_after_market(hm, expect):
    """16:00~16:20은 이날 정규장, 16:21~16:29·16:31~16:39는 분류하지 않는다(애프터마켓이 아님)."""
    r = classify(csat_stock_day([row(hm, 50750, v=3)]))
    if expect == "regular":
        assert r.status == "ok" and r.sessions[sb.REGULAR].bar_count == 4 and r.sessions[sb.AFTER_MARKET].first_bar_at.hour == 17
    else:
        assert r.status == "rejected" and hm in r.unclassified


def test_csat_missing_1630_with_late_bar_is_rejected_not_guessed():
    rows = [r for r in csat_stock_day() if not r["localDateTime"].endswith("163000")] + [row("1632", 50700, v=4000)]
    r = classify(rows)
    assert r.status == "rejected" and "1632" in r.unclassified  # 이날은 지연 창이 없다


def test_csat_etf_has_no_after_market_and_none_is_created():
    etf = [row("1000", 10000), row("1620", 10010), row("1630", 10005, v=50), row("1640", 10005, v=2)]
    r = classify(etf, asset="etf")
    assert r.status == "ok" and set(r.sessions) == {sb.REGULAR, sb.CLOSING_AUCTION, sb.POST_CLOSE}
    r2 = classify(etf + [row("1700", 10000)], asset="etf")
    assert r2.status == "rejected" and r2.unclassified == ["1700"]


def test_csat_day_data_that_followed_the_normal_schedule_is_rejected():
    """그날 실제로 정규 일정(09:00 개장·15:30 마감·16:00 애프터마켓)대로 거래됐다면, 수능 일정으로 받아들이지 않는다:
    09시대 봉과 16:21 뒤 애프터마켓 봉이 이날 일정의 어느 세션에도 맞지 않는다. (15:30·15:40 봉은 이날 정규장 시간이라 그 자체로는 정상)"""
    normal_pattern = [row("0900", 100, v=10), row("1100", 101), row("1520", 101), row("1530", 101, v=500), row("1540", 101, v=3),
                      row("1600", 101), row("1700", 102), row("1959", 101)]
    r = classify(normal_pattern)
    assert r.status == "rejected" and "0900" in r.unclassified
    # 09시 봉이 없어도, 정규 일정이면 16:00~16:59에 애프터마켓 연속 체결 봉이 이어진다(관찰: 삼성전자 11-12 238봉)
    no_open = normal_pattern[1:] + [row("1625", 101, 102, 100, 101), row("1650", 101, 102, 100, 100)]
    r2 = classify(no_open)
    assert r2.status == "rejected" and {"1625", "1650"} <= set(r2.unclassified)
    only_mid = [row("1530", 101), row("1540", 101)]
    assert classify(only_mid).sessions[sb.REGULAR].bar_count == 2  # 이날 15:30·15:40은 정규장 연속매매


def test_csat_incomplete_collection_rejected():
    r = classify(csat_stock_day(), collected=datetime(2026, 11, 19, 20, 5, tzinfo=KST))
    assert r.status == "rejected" and "incomplete_day" in r.reasons


# ---------- 집계 공식(정규 일정, 실제 관찰 모양) ----------

def test_ohlcv_formulas_first_max_min_last_sum():
    rows = [row("0900", 100, 105, 99, 104, 10, day="20261112"), row("1000", 104, 110, 103, 108, 20, day="20261112"),
            row("1100", 108, 109, 95, 97, 30, day="20261112"), row("1520", 97, 98, 96, 98, 5, day="20261112")]
    r = classify(rows, asset="etf", d=NORMAL)
    reg = r.sessions[sb.REGULAR]
    assert (reg.open, reg.high, reg.low, reg.close, reg.volume, reg.bar_count) == (100, 110, 95, 98, 65, 4)


def test_closing_auction_and_post_close_are_never_merged():
    rows = [row("0900", 100, day="20261112"), row("1530", 101, v=5000, day="20261112"), row("1540", 101, v=7, day="20261112"),
            row("1559", 101, v=3, day="20261112")]
    r = classify(rows, asset="etf", d=NORMAL)
    assert r.sessions[sb.CLOSING_AUCTION].volume == 5000 and r.sessions[sb.CLOSING_AUCTION].bar_count == 1
    assert r.sessions[sb.POST_CLOSE].volume == 10 and r.sessions[sb.POST_CLOSE].bar_count == 2


def test_multiple_late_auction_candidates_are_rejected():
    rows = [row("0900", 100, day="20261112"), row("1532", 101, day="20261112"), row("1534", 101, day="20261112")]
    r = classify(rows, d=NORMAL)
    assert r.status == "rejected" and {"1532", "1534"} <= set(r.unclassified)


def test_regression_284430_negative_minute_volume_rejects_whole_day():
    """실제 원천(284430, 2026-11-10 10:18 거래량 -8, 소수 12자리 가격)의 모양. 그 봉만 빼고 집계하지 않는다."""
    rows = [row("0900", Decimal("17930.0"), day="20261110"), row("1017", Decimal("17925.0"), v=12, day="20261110"),
            {"localDateTime": "20261110101800", "currentPrice": Decimal("17910.0"), "openPrice": Decimal("17929.949874686718"),
             "highPrice": Decimal("17929.949874686718"), "lowPrice": Decimal("17910.0"), "accumulatedTradingVolume": -8},
            row("1530", Decimal("17920.0"), v=900, day="20261110")]
    data = json.dumps(rows, default=str).replace('"17929.949874686718"', "17929.949874686718")
    data = data.replace('"17930.0"', "17930.0").replace('"17925.0"', "17925.0").replace('"17910.0"', "17910.0").replace('"17920.0"', "17920.0")
    by, probs, _ = sb.parse_bars(sb.load_rows(data.encode()))
    d = date(2026, 11, 10)
    r = sb.classify_day(d, by.get(d, []), "etf", datetime(2026, 11, 14, 11, 54, tzinfo=KST), probs.get(d))
    assert r.status == "rejected" and r.reasons == ["bad_values"] and r.sessions == {}


def test_out_of_order_source_is_rejected_not_silently_sorted():
    rows = [row("1000", 100, day="20261112"), row("0900", 100, day="20261112"), row("1530", 100, day="20261112")]
    by, probs, _ = sb.parse_bars(sb.load_rows(json.dumps(rows).encode()))
    r = sb.classify_day(NORMAL, by[NORMAL], "etf", datetime(2026, 11, 12, 21, tzinfo=KST), probs.get(NORMAL))
    assert r.status == "rejected" and "out_of_order" in r.reasons
