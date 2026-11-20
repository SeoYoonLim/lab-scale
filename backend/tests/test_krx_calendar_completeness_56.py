"""56차: KRX 달력 개장 시각(regular_open·special_sessions.open), 장 단계(market_phase), 지수 보류 사유 분리, 환율 기준 분리,
평가기(evals)의 마감 시각. 순수 함수(시각 주입). 네트워크·DB·현재 시각을 쓰지 않는다. 11-19 값은 합성이다(실제 데이터는 아직 없다).
"""
import ast
import copy
import json
from datetime import date, datetime, time, timedelta
from pathlib import Path

import pytest

from app import briefing, data_monitor, index_confirm as ic, krx_calendar as K, market_snapshot as ms, outlook, session_ohlcv as so

KST = K.KST
CSAT = date(2026, 11, 19)
WEEK = [date(2026, 11, 16), date(2026, 11, 17), date(2026, 11, 18), CSAT, date(2026, 11, 20)]
REGULAR_DAYS = [d for d in WEEK if d != CSAT]
RAW = json.loads(K.DATA_PATH.read_text(encoding="utf-8"))


def at(d: date, hms: str) -> datetime:
    h, m, s = (list(map(int, hms.split(":"))) + [0])[:3]
    return datetime(d.year, d.month, d.day, h, m, s, tzinfo=KST)


# ---------- 1. 달력 구조와 호환성 ----------

def test_open_times_in_calendar_file():
    assert RAW["regular_open"] == "09:00" and RAW["regular_close"] == "15:30"
    assert [(s["date"], s.get("open"), s["close"]) for s in RAW["special_sessions"]] == [("2026-11-19", "10:00", "16:30")]
    cal = K.calendar()
    assert cal.regular_open == time(9, 0) and cal.special_open == {CSAT: time(10, 0)}
    assert K.open_time(CSAT) == time(10, 0) and K.close_time(CSAT) == time(16, 30)


def test_every_other_day_uses_default_open_and_close():
    cal = K.calendar()
    days = [d for d in K.trading_days(cal.start, cal.end) if d != CSAT]
    assert {(K.open_time(d), K.close_time(d)) for d in days} == {(time(9, 0), time(15, 30))}
    assert K.open_time(date(2027, 1, 4)) == time(9, 0)  # 범위 밖(등록 안 된 날)은 기본값


def test_special_day_without_open_uses_regular_open():
    data = copy.deepcopy(RAW)
    del data["special_sessions"][0]["open"]
    cal = K.parse(data)
    assert K.open_time(CSAT, cal) == time(9, 0) and K.close_time(CSAT, cal) == time(16, 30)


def test_file_without_regular_open_still_loads_with_0900():
    data = copy.deepcopy(RAW)
    del data["regular_open"]
    assert K.parse(data).regular_open == time(9, 0)


def test_calendar_constructed_with_old_fields_still_works():
    """55차까지의 Calendar(...) 위치 인자 9개(개장 필드 없음)로 만든 달력도 동작한다."""
    c = K.calendar()
    old = K.Calendar(c.version, c.updated, c.start, c.end, c.regular_close, c.holidays, c.unconfirmed, c.special_close, c.fixed_annual)
    assert K.open_time(CSAT, old) == time(9, 0) and K.close_time(CSAT, old) == time(16, 30)


@pytest.mark.parametrize("mutate", [
    lambda d: d["special_sessions"][0].update(open="16:30"),       # 개장 = 마감
    lambda d: d["special_sessions"][0].update(open="17:00"),       # 개장 > 마감
    lambda d: d["special_sessions"][0].update(open="10:0"),        # 형식
    lambda d: d["special_sessions"][0].update(open=1000),          # 문자열 아님
    lambda d: d["special_sessions"][0].update(open="10:00:00"),    # 초는 쓰지 않는다
    lambda d: d["special_sessions"][0].update(close="08:30") or d["special_sessions"][0].pop("open"),  # open 없으면 09:00 >= 08:30
    lambda d: d.update(regular_open="15:30"),                      # 기본 개장 = 기본 마감
    lambda d: d.update(regular_open="9:00"),                       # 기본 개장 형식
    lambda d: d.update(regular_close="1530"),                      # 기본 마감 형식
    lambda d: d["special_sessions"].append(copy.deepcopy(d["special_sessions"][0])),  # 같은 날짜 두 번(55차 규칙 유지)
    lambda d: d["special_sessions"][0].update(date="2026-11-21"),  # 주말(55차 규칙 유지)
])
def test_invalid_open_settings_are_rejected(mutate):
    data = copy.deepcopy(RAW)
    mutate(data)
    with pytest.raises(ValueError):
        K.parse(data)


# ---------- 2. 장 단계(달력만) ----------

@pytest.mark.parametrize("hms,phase", [
    ("08:59:59", K.PRE_MARKET), ("09:00:00", K.PRE_MARKET), ("09:59:59", K.PRE_MARKET),
    ("10:00:00", K.INTRADAY), ("16:29:59", K.INTRADAY),
    ("16:30:00", K.CLOSED), ("16:39:59", K.CLOSED), ("16:40:00", K.CLOSED),
])
def test_csat_market_phase(hms, phase):
    assert K.market_phase(at(CSAT, hms)) == phase


@pytest.mark.parametrize("d", REGULAR_DAYS)
@pytest.mark.parametrize("hms,phase", [
    ("08:59:59", K.PRE_MARKET), ("09:00:00", K.INTRADAY), ("15:29:59", K.INTRADAY), ("15:30:00", K.CLOSED), ("15:40:00", K.CLOSED),
])
def test_regular_day_market_phase(d, hms, phase):
    assert K.market_phase(at(d, hms)) == phase


def test_non_trading_days_and_unknown_days():
    for d in (date(2026, 11, 21), date(2026, 11, 22), date(2026, 12, 25), date(2026, 12, 31)):  # 58차: 12-31 연말 휴장 확정
        assert K.market_phase(at(d, "11:00")) == K.NON_TRADING_DAY
    assert K.market_phase(at(date(2027, 1, 4), "11:00")) == K.INTRADAY  # 확인할 수 없는 날(unknown)은 거래일일 수 있는 날, 기본 시각


def test_session_is_open_keeps_meaning_before_close_including_pre_market():
    """공개 API의 intraday_excluded·일일 브리핑 intraday는 session_is_open이다. 장 시작 전에도 True(오늘 종가 없음) 그대로다."""
    for d in WEEK:
        assert K.session_is_open(at(d, "08:00")) is True
    assert K.session_is_open(at(CSAT, "09:30")) is True and K.market_phase(at(CSAT, "09:30")) == K.PRE_MARKET
    assert K.expected_trading_day(at(CSAT, "09:30")) == date(2026, 11, 18)


# ---------- 3. 지수 보류 사유(내부 상태) ----------

NAVER = {date(2026, 11, 18): 3990.55, CSAT: 4000.12}


def recs(day, v=4000.12):
    return [{"price_date": K.previous_trading_day(day), "close_price": 3990.55}, {"price_date": day, "close_price": v}]


@pytest.mark.parametrize("hms,status", [
    ("08:59:59", ic.PRE_MARKET), ("09:00:00", ic.PRE_MARKET), ("09:59:59", ic.PRE_MARKET),
    ("10:00:00", ic.INTRADAY), ("16:29:59", ic.INTRADAY),
    ("16:30:00", ic.CONFIRMATION_PENDING), ("16:39:59", ic.CONFIRMATION_PENDING),
    ("16:40:00", ic.CONFIRMED),  # 확정 검증 시도 가능 -> 네이버와 같아서 확정
])
def test_csat_index_status(hms, status):
    now = at(CSAT, hms)
    got = ic.decide(recs(CSAT), NAVER, now)[-1]
    assert got.status == status
    assert got.held is (status != ic.CONFIRMED)
    assert ic.intraday(CSAT, now) is (status in ic.TIME_HELD)  # 50차 함수의 뜻 그대로


@pytest.mark.parametrize("d", REGULAR_DAYS)
@pytest.mark.parametrize("hms,status", [
    ("08:59:59", ic.PRE_MARKET), ("09:00:00", ic.INTRADAY), ("15:29:59", ic.INTRADAY),
    ("15:30:00", ic.CONFIRMATION_PENDING), ("15:39:59", ic.CONFIRMATION_PENDING), ("15:40:00", ic.CONFIRMED),
])
def test_regular_day_index_status(d, hms, status):
    naver = {K.previous_trading_day(d): 3990.55, d: 4000.12}
    assert ic.decide(recs(d), naver, at(d, hms))[-1].status == status


def test_confirmed_only_after_real_cross_check():
    now = at(CSAT, "16:40")
    assert ic.decide(recs(CSAT, 4011.0), NAVER, now)[-1].status == ic.MISMATCH
    assert ic.decide(recs(CSAT), {date(2026, 11, 18): 3990.55}, now)[-1].status == ic.UNVERIFIED
    assert ic.decide(recs(CSAT), None, now)[-1].status == ic.UNVERIFIED
    assert all(s in ic.HELD for s in (ic.PRE_MARKET, ic.INTRADAY, ic.CONFIRMATION_PENDING, ic.MISMATCH, ic.UNVERIFIED))
    assert ic.CONFIRMED not in ic.HELD and ic.TRUSTED_OLDER not in ic.HELD


def test_previous_day_row_is_not_time_held_in_the_morning():
    # 11-19 개장 전 실행: 11-18 행은 시각 보류 없이 대조한다(장 시작 전 상태는 오늘 행에만)
    out = ic.decide(recs(CSAT)[:1], NAVER, at(CSAT, "09:30"))
    assert [x.status for x in out] == [ic.CONFIRMED]


def test_held_rows_keep_existing_values(monkeypatch):
    import pandas as pd

    from app import collectors

    monkeypatch.setattr(collectors, "SessionLocal", lambda: pytest.fail("DB를 열면 안 된다"))
    frame = pd.DataFrame({"Close": [4000.12], "Change": [0.0]}, index=pd.to_datetime(["2026-11-19"]))
    for hms, why in (("09:30", ic.PRE_MARKET), ("12:00", ic.INTRADAY), ("16:31", ic.CONFIRMATION_PENDING)):
        res = collectors.fetch_and_save_market_index(
            "KS11", start_date="2026-11-19", end_date="2026-11-19", now=at(CSAT, hms), reader=lambda s, a, b: frame,
            naver_get=lambda u, p: [{"localTradedAt": "2026-11-19", "closePrice": "4,000.12"}])
        assert res.ok and res.saved == 0 and [h["status"] for h in res.held_dates] == [why]


# ---------- 4. 운영 스크립트·50차 감사 모듈 ----------

def test_refresh_market_hours_follow_open_and_close():
    from scripts import refresh_data as rd

    assert [rd.is_market_hours(at(CSAT, t)) for t in ("08:59", "09:00", "09:59", "10:00", "16:30", "16:31")] == \
        [False, False, False, True, True, False]
    for d in REGULAR_DAYS:
        assert [rd.is_market_hours(at(d, t)) for t in ("08:59", "09:00", "15:30", "15:31")] == [False, True, True, False]


def test_session_ohlcv_refuses_special_session_day():
    later = at(CSAT, "21:00")
    bars = [{"localDateTime": "20261119100000", "openPrice": 1, "highPrice": 1, "lowPrice": 1, "currentPrice": 1,
             "accumulatedTradingVolume": 1}]
    sessions = so.aggregate(bars).get(CSAT, {})
    assert so.regular_issue(sessions, CSAT, later) == "special_session_day"
    assert so.compare_day({"open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}, sessions, CSAT, later)["issue"] == "special_session_day"
    assert so.regular_issue({}, date(2026, 11, 18), at(date(2026, 11, 18), "21:00")) == "no_regular_bars"  # 일반 거래일 판정 그대로


# ---------- 5. 환율: KRX 달력과 분리된 평일 기준 ----------

def test_fx_expected_day_is_independent_of_krx_special_close():
    fx = data_monitor.SOURCE_BY_KEY["exchange_rate"]
    stock = data_monitor.SOURCE_BY_KEY["stock_price"]
    assert fx.freshness == "fx_weekday_close" and stock.freshness == "krx_trading_close"
    # 11-19 16:00: KRX는 장중(기대 11-18), 환율 평일 정책은 15:30 기준이라 11-19 그대로(56차: 16:30을 적용하지 않는다)
    assert data_monitor.expected_as_of(stock, at(CSAT, "16:00")) == date(2026, 11, 18)
    assert data_monitor.expected_as_of(fx, at(CSAT, "16:00")) == CSAT
    assert data_monitor.expected_as_of(fx, at(CSAT, "15:29")) == date(2026, 11, 18)
    # KRX 휴장 평일(12-25 금): 환율은 그날을 기대(원천이 한국 휴장일에도 값을 준다), KRX는 12-24
    assert data_monitor.expected_as_of(fx, at(date(2026, 12, 25), "16:00")) == date(2026, 12, 25)
    assert data_monitor.expected_as_of(stock, at(date(2026, 12, 25), "16:00")) == date(2026, 12, 24)


@pytest.mark.parametrize("d", REGULAR_DAYS)
def test_fx_expected_day_regular_days_unchanged(d):
    fx = data_monitor.SOURCE_BY_KEY["exchange_rate"]
    assert data_monitor.expected_as_of(fx, at(d, "15:29")) == K.previous_trading_day(d)  # 이 주에는 휴장 평일이 없어 직전 평일과 같다
    assert data_monitor.expected_as_of(fx, at(d, "15:30")) == d


def test_briefing_shows_fx_with_the_same_base_rule_as_market_data():
    """브리핑 화면은 환율 오늘 행에도 drop_open_session(KRX 달력)을 쓴다(시세·지수와 같은 기준일을 보이려고, 36차부터). 56차 변경 없음."""
    rows = [(date(2026, 11, 18), 1390.0), (CSAT, 1391.0)]
    assert [r[0] for r in briefing.drop_open_session(rows, at(CSAT, "16:00"))] == [date(2026, 11, 18)]
    assert [r[0] for r in briefing.drop_open_session(rows, at(CSAT, "16:30"))] == [date(2026, 11, 18), CSAT]


# ---------- 6. 시장 요약·일일 브리핑 기준일 일치 ----------

@pytest.mark.parametrize("d", WEEK + [date(2026, 11, 21), date(2026, 12, 25)])
@pytest.mark.parametrize("hms", ["08:00", "09:30", "10:00", "15:29", "15:30", "16:00", "16:29", "16:30", "16:40", "21:00"])
def test_market_summary_and_daily_briefing_agree(d, hms):
    now = at(d, hms)
    expected = K.expected_trading_day(now)
    assert outlook.expected_trading_day(now) == expected  # 일일 브리핑·전망·단순 시세 답
    assert data_monitor.expected_close_date(now) == expected  # 운영 보고서
    open_ = K.session_is_open(now)
    assert briefing.session_is_open(now) is open_  # 일일 브리핑 intraday·브리핑 장중 행 제외
    assert ms.cutoff_date(now) == (now.date() - timedelta(days=1) if open_ else now.date())  # 시장 요약 intraday_excluded


def test_public_status_values_unchanged():
    out = ms.build({"KS11": [(date(2026, 11, 18), 4000.0, None), (date(2026, 11, 17), 3990.0, None)]}, date(2026, 11, 18),
                   {"KOSPI": (100, 150, 10, 0, 260)}, at(CSAT, "09:30"))
    assert out["freshness"]["intraday_excluded"] is True  # 장 시작 전도 그대로 True
    assert out["freshness"]["status"] in (ms.STATUS_LATEST, ms.STATUS_DELAYED)
    assert out["expected_date"] == date(2026, 11, 18)


# ---------- 7. 평가기(evals): 제품 코드 없이 달력 파일에서 마감 시각 ----------

def test_evaluator_close_matches_calendar_for_every_day():
    from evals import quality_judges as J

    cal = K.calendar()
    for d in K.trading_days(cal.start, cal.end):
        assert J.krx_close_hhmm(d) == K.close_time(d).strftime("%H:%M")
    assert J.krx_close_hhmm(CSAT) == "16:30" and J.krx_close_hhmm(date(2026, 11, 18)) == "15:30"


def test_quality_judges_stays_independent_of_product_calendar_code():
    tree = ast.parse(Path(__import__("evals.quality_judges").quality_judges.__file__).read_text(encoding="utf-8"))
    mods = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)} | \
           {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    assert not any(m and (m == "app" or m.startswith("app.")) for m in mods)


def test_evaluator_expected_day_and_visible_rows_follow_special_close():
    from evals import forecast_check as FC, quality_judges as J, run_quality as RQ

    assert J.expected_trading_day(CSAT, "16:00") == date(2026, 11, 18)
    assert J.expected_trading_day(CSAT, "16:30") == CSAT
    assert J.expected_trading_day(date(2026, 11, 18), "15:29") == date(2026, 11, 17)
    assert J.expected_trading_day(date(2026, 11, 18), "15:30") == date(2026, 11, 18)
    rows = [(date(2026, 11, 18), 1.0), (CSAT, 2.0)]
    assert RQ._visible_rows(rows, at(CSAT, "16:00")) == rows[:1]
    assert RQ._visible_rows(rows, at(CSAT, "16:30")) == rows
    assert RQ._visible_rows([(date(2026, 11, 18), 1.0)], at(date(2026, 11, 18), "15:30")) == [(date(2026, 11, 18), 1.0)]
    assert FC._before_close24("2026-11-19 16:00") is True and FC._before_close24("2026-11-19 16:30") is False
    assert FC._before_close24("2026-11-18 15:29") is True and FC._before_close24("2026-11-18 15:30") is False
    assert FC._expected_day24("2026-11-19 16:00") == date(2026, 11, 18)
    assert FC._expected_day24("2026-11-19 16:30") == CSAT
    assert FC._expected_day24("2026-11-16 10:00") == date(2026, 11, 13)  # 월요일 장중 -> 금요일(평일 규칙 그대로)
