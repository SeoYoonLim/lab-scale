"""55차: KRX 특수 마감(2026-11-19 수능일 정규시장 16:30)을 기존 달력(app/data/krx_calendar.json)에 넣고 기존 서비스 판정을 검증한다.

순수 함수(시각 주입). 네트워크·DB·현재 시각을 쓰지 않는다. 지수 값·네이버 값은 합성이다(11-19 실제 데이터는 아직 없다).
- close_time()은 정규시장 마감(= 종가 단일가 체결 시각)이다. 장후 시간외(16:40~17:00)·애프터마켓(17:00~20:00) 종료가 아니다.
- 지수 확정 검증은 마감 + 10분(CONFIRM_DELAY)부터 "시도"할 수 있을 뿐이다. 네이버 대조 조건을 만족해야 저장한다.
"""
import copy
import json
from datetime import date, datetime, time, timedelta

import pytest

from app import briefing, code_answers, index_confirm as ic, krx_calendar as K, market_snapshot as ms, session_schedule as ss
from app import data_monitor, event_reaction, official_close as oc, outlook

KST = K.KST
CSAT = date(2026, 11, 19)
REGULAR_DAYS = [date(2026, 11, 16), date(2026, 11, 17), date(2026, 11, 18), date(2026, 11, 20)]
RAW = json.loads(K.DATA_PATH.read_text(encoding="utf-8"))


def at(d: date, hms: str) -> datetime:
    h, m, s = (list(map(int, hms.split(":"))) + [0])[:3]
    return datetime(d.year, d.month, d.day, h, m, s, tzinfo=KST)


def recs(day: date, value: float = 4000.12) -> list[dict]:
    prev = K.previous_trading_day(day)
    return [{"price_date": prev, "close_price": 3990.55}, {"price_date": day, "close_price": value}]


# ---------- 1. 달력 JSON ----------

def test_calendar_has_exactly_one_special_close_for_csat():
    assert RAW["special_sessions"] == [{"date": "2026-11-19", "open": "10:00", "close": "16:30", "name": "2027학년도 대학수학능력시험일(정규시장 10:00~16:30)",
                                        "source": "krx_notice"}]
    assert RAW["special_sessions"][0]["source"] in RAW["sources"]
    cal = K.calendar()
    assert cal.special_close == {CSAT: time(16, 30)}
    assert cal.regular_close == time(15, 30)
    assert K.close_time(CSAT) == time(16, 30)


def test_every_other_trading_day_in_coverage_keeps_15_30():
    cal = K.calendar()
    days = K.trading_days(cal.start, cal.end)
    assert len(days) > 700
    assert {K.close_time(d) for d in days if d != CSAT} == {time(15, 30)}


def test_close_is_regular_session_close_not_after_market_end():
    # 애프터마켓 종료(20:00)·장후 종가매매 종료(17:00)를 close에 넣지 않는다
    assert K.close_time(CSAT) < time(16, 40)


def _bad(mutate):
    data = copy.deepcopy(RAW)
    mutate(data)
    with pytest.raises(ValueError):
        K.parse(data)


@pytest.mark.parametrize("mutate", [
    lambda d: d["special_sessions"].append(copy.deepcopy(d["special_sessions"][0])),     # 같은 날짜 두 번
    lambda d: d["special_sessions"][0].update(date="2026-11-21"),                          # 토요일
    lambda d: d["special_sessions"][0].update(date="2026-12-25"),                          # 휴장일
    lambda d: d["special_sessions"][0].update(date="2027-01-04"),                          # 달력 범위 밖
    lambda d: d["special_sessions"][0].update(close="16:3"),                               # 시각 형식
    lambda d: d["special_sessions"][0].update(close="24:00"),                              # 시각 범위
    lambda d: d["special_sessions"][0].update(close="16:30:00"),                           # 초는 쓰지 않는다
])
def test_invalid_special_sessions_are_rejected(mutate):
    _bad(mutate)


def test_parsing_the_real_file_is_unchanged_for_holidays_and_coverage():
    cal = K.parse(RAW)
    assert (cal.start, cal.end) == (date(2023, 10, 30), date(2026, 12, 31))
    assert len(cal.holidays) == 56 and list(cal.unconfirmed) == []  # 58차: 2026-12-31 미확정 -> 연말 휴장


# ---------- 2. 지수 확정 검증 경계값(15:40~16:41) ----------

NAVER_SAME = {K.previous_trading_day(CSAT): 3990.55, CSAT: 4000.12}  # 장중 값이 우연히 네이버와 같아도


@pytest.mark.parametrize("hms,intraday,status", [
    ("15:39:59", True, ic.INTRADAY),
    ("15:40:00", True, ic.INTRADAY),   # 변경 전: 15:30 + 10분이라 여기서 confirmed가 됐다
    ("16:00:00", True, ic.INTRADAY),
    ("16:29:59", True, ic.INTRADAY),
    ("16:30:00", True, ic.CONFIRMATION_PENDING),   # 정규장 마감 도달, 확정 대기(56차: intraday와 나눔)
    ("16:39:59", True, ic.CONFIRMATION_PENDING),   # 10분 확인 지연 안
    ("16:40:00", False, ic.CONFIRMED),  # 확정 검증 시도 가능(네이버와 같아서 확정)
    ("16:41:00", False, ic.CONFIRMED),
])
def test_csat_index_confirmation_boundaries(hms, intraday, status):
    now = at(CSAT, hms)
    assert ic.intraday(CSAT, now) is intraday
    decisions = ic.decide(recs(CSAT), NAVER_SAME, now)
    assert decisions[-1].status == status
    keep, held = ic.split(recs(CSAT), NAVER_SAME, now)
    assert (CSAT in [r["price_date"] for r in keep]) is (status == ic.CONFIRMED)
    assert decisions[0].status == ic.CONFIRMED  # 전날 행은 시각과 상관없이 대조로 확정


@pytest.mark.parametrize("hms", ["16:40:00", "16:41:00", "18:00:00", "23:30:00"])
def test_after_1640_time_alone_never_confirms(hms):
    """16:40을 넘었다는 이유만으로 저장하지 않는다: 네이버와 다르거나(mismatch), 네이버에 없거나 읽지 못하면(unverified) 보류."""
    now = at(CSAT, hms)
    mismatch = ic.decide(recs(CSAT, 4012.00), NAVER_SAME, now)[-1]
    assert mismatch.status == ic.MISMATCH and mismatch.held and mismatch.reference == 4000.12
    missing = ic.decide(recs(CSAT), {K.previous_trading_day(CSAT): 3990.55}, now)[-1]
    assert missing.status == ic.UNVERIFIED and missing.held
    unreadable = ic.decide(recs(CSAT), None, now)
    assert unreadable[-1].status == ic.UNVERIFIED and unreadable[0].status == ic.TRUSTED_OLDER
    within = ic.decide(recs(CSAT, 4000.124), NAVER_SAME, now)[-1]  # 허용 오차(0.005) 안
    assert within.status == ic.CONFIRMED


def test_no_collected_row_means_nothing_to_confirm():
    assert ic.decide([], NAVER_SAME, at(CSAT, "16:41")) == []
    keep, held = ic.split([], NAVER_SAME, at(CSAT, "16:41"))
    assert keep == [] and held == []


def test_held_csat_row_is_never_written(monkeypatch):
    """확정 실패(장중 보류) 시 DB를 열지 않는다 = 기존 값 유지(50차 수집 경로 그대로)."""
    import pandas as pd

    from app import collectors

    monkeypatch.setattr(collectors, "SessionLocal", lambda: pytest.fail("DB를 열면 안 된다"))
    frame = pd.DataFrame({"Close": [4000.12], "Change": [0.0]}, index=pd.to_datetime(["2026-11-19"]))
    for hms, why in (("16:00", ic.INTRADAY), ("16:35", ic.CONFIRMATION_PENDING)):
        res = collectors.fetch_and_save_market_index(
            "KS11", start_date="2026-11-19", end_date="2026-11-19", now=at(CSAT, hms), reader=lambda s, a, b: frame,
            naver_get=lambda u, p: [{"localTradedAt": "2026-11-19", "closePrice": "4,000.12"}])
        assert res.ok and res.saved == 0 and res.held_dates[0]["status"] == why
    res = collectors.fetch_and_save_market_index(
        "KS11", start_date="2026-11-19", end_date="2026-11-19", now=at(CSAT, "16:41"), reader=lambda s, a, b: frame,
        naver_get=lambda u, p: [{"localTradedAt": "2026-11-19", "closePrice": "4,011.00"}])
    assert res.ok and res.saved == 0 and res.held_dates[0]["status"] == ic.MISMATCH


# ---------- 3. 장중·신선도 판정 ----------

@pytest.mark.parametrize("hms,open_,expected", [
    ("09:30", True, date(2026, 11, 18)),
    ("15:30", True, date(2026, 11, 18)),   # 변경 전: 마감 뒤로 봤다
    ("15:31", True, date(2026, 11, 18)),
    ("16:29:59", True, date(2026, 11, 18)),
    ("16:30", False, CSAT),
    ("16:39:59", False, CSAT),
    ("16:40", False, CSAT),
    ("20:30", False, CSAT),
])
def test_csat_session_and_expected_trading_day(hms, open_, expected):
    now = at(CSAT, hms)
    assert K.session_is_open(now) is open_
    assert briefing.session_is_open(now) is open_
    assert K.expected_trading_day(now) == expected
    assert outlook.expected_trading_day(now) == expected
    assert data_monitor.expected_close_date(now) == expected
    assert ms.cutoff_date(now) == (CSAT - timedelta(days=1) if open_ else CSAT)
    rows = [(date(2026, 11, 18), 1.0), (CSAT, 2.0)]
    assert [r[0] for r in briefing.drop_open_session(rows, now)] == ([date(2026, 11, 18)] if open_ else [date(2026, 11, 18), CSAT])


def test_csat_freshness_lag_counts_today_only_after_16_30():
    # 11-18 종가까지 있는 데이터: 16:30 전에는 최신(0), 16:30부터 1거래일 지연
    assert K.lag(date(2026, 11, 18), K.expected_trading_day(at(CSAT, "16:10"))).days == 0
    assert K.lag(date(2026, 11, 18), K.expected_trading_day(at(CSAT, "16:30"))).days == 1


def test_price_status_counts_refresh_runs_only_after_the_real_close():
    """outlook.price_status: 11-19 16:00(장중) 수집 기록은 '장 마감 뒤 수집'이 아니다(변경 전에는 15:30 기준이라 휴장일 추정으로 잘못 분류)."""
    from types import SimpleNamespace

    class Q:
        def __init__(self, runs):
            self.runs = runs

        def filter(self, *a):
            return self

        order_by = limit = filter

        def scalar(self):
            return None

        def all(self):
            return self.runs

    def db(runs):
        return SimpleNamespace(query=lambda *a: Q(runs))

    during = [SimpleNamespace(status="success", started_at=at(CSAT, "16:00"))]
    out = outlook.price_status(db(during), date(2026, 11, 18), at(CSAT, "21:00"))
    assert out["status"] == outlook.PRICE_STATUS_NOT_COLLECTED
    after = [SimpleNamespace(status="success", started_at=at(CSAT, "16:45"))]
    # 최종 점검(의도된 기대 변경): 11-19는 확정 거래일이라 휴장일 추정이 아니라 공식 종가 대기다(마감 판정 16:30은 그대로 검사)
    assert outlook.price_status(db(after), date(2026, 11, 18), at(CSAT, "21:00"))["status"] == outlook.PRICE_STATUS_OFFICIAL_PENDING
    # 일반 거래일은 그대로 15:30
    normal = [SimpleNamespace(status="success", started_at=at(date(2026, 11, 18), "16:00"))]
    assert (outlook.price_status(db(normal), date(2026, 11, 17), at(date(2026, 11, 18), "21:00"))["status"]
            == outlook.PRICE_STATUS_OFFICIAL_PENDING)


def test_etf_latest_row_is_intraday_until_16_30_on_csat():
    def row(d, c):
        return {"localTradedAt": d, "closePrice": f"{c:,}", "compareToPreviousClosePrice": "0", "openPrice": f"{c:,}",
                "highPrice": f"{c:,}", "lowPrice": f"{c:,}", "accumulatedTradingVolume": 1000}

    rows = [row("2026-11-18", 82000), row("2026-11-19", 84000)]
    for hms, n in (("16:00", 1), ("16:29", 1), ("16:30", 2)):
        out, _ = oc.official_records("069500", "etf", date(2026, 11, 18), rows=rows, now=datetime(2026, 11, 19, *map(int, hms.split(":"))))
        assert len(out) == n, hms
    rows18 = [row("2026-11-17", 82000), row("2026-11-18", 84000)]
    out, _ = oc.official_records("069500", "etf", date(2026, 11, 17), rows=rows18, now=datetime(2026, 11, 18, 15, 31))
    assert len(out) == 2  # 일반 거래일은 15:30 뒤 그대로


def test_news_during_extended_session_reacts_same_day():
    dates = [date(2026, 11, 18), CSAT, date(2026, 11, 20)]
    assert event_reaction.resolve_base_and_start(dates, at(CSAT, "16:00"), None) == (date(2026, 11, 18), CSAT)
    assert event_reaction.resolve_base_and_start(dates, at(CSAT, "16:30"), None) == (CSAT, date(2026, 11, 20))
    d18 = [date(2026, 11, 17), date(2026, 11, 18), CSAT]
    assert event_reaction.resolve_base_and_start(d18, at(date(2026, 11, 18), "16:00"), None) == (date(2026, 11, 18), CSAT)


def test_intraday_note_names_the_days_close():
    def rec(day):
        return [{"result": {"intraday_excluded": day, "prices": [{"price_date": "2026-11-18"}]}}]

    assert code_answers.intraday_note(rec("2026-11-19")) == "오늘(2026-11-19)은 장 마감(16:30) 전이라 직전 거래일 2026-11-18 종가 기준입니다."
    assert "장 마감(15:30) 전" in code_answers.intraday_note(rec("2026-11-18"))


def test_scripts_market_hours_and_backfill_end_follow_csat_close():
    from scripts import backfill_history as bf, refresh_data as rd

    assert rd.is_market_hours(at(CSAT, "16:00")) is True
    assert rd.is_market_hours(at(CSAT, "16:30")) is True   # 일반 거래일 15:30 포함 규칙과 같게 마감 분 포함
    assert rd.is_market_hours(at(CSAT, "16:31")) is False
    assert rd.is_market_hours(at(date(2026, 11, 18), "15:31")) is False
    assert bf.period(at(CSAT, "16:00"), 1)[1] == date(2026, 11, 18)
    assert bf.period(at(CSAT, "16:30"), 1)[1] == CSAT
    assert bf.period(at(date(2026, 11, 18), "15:30"), 1)[1] == date(2026, 11, 18)


# ---------- 4. 일반 거래일 회귀 ----------

@pytest.mark.parametrize("d", REGULAR_DAYS)
def test_regular_days_keep_15_30(d):
    assert K.day_status(d) == K.TRADING and K.close_time(d) == time(15, 30)
    assert K.session_is_open(at(d, "15:29:59")) is True
    assert K.session_is_open(at(d, "15:30")) is False
    assert K.expected_trading_day(at(d, "15:29:59")) == K.previous_trading_day(d)
    assert K.expected_trading_day(at(d, "15:30")) == d
    assert ic.intraday(d, at(d, "15:39:59")) is True
    assert ic.intraday(d, at(d, "15:40")) is False
    naver = {K.previous_trading_day(d): 3990.55, d: 4000.12}
    assert ic.decide(recs(d), naver, at(d, "15:40"))[-1].status == ic.CONFIRMED
    assert ic.decide(recs(d), naver, at(d, "16:00"))[-1].status == ic.CONFIRMED


def test_non_trading_and_unregistered_days():
    sat, hol, unconfirmed, outside = date(2026, 11, 21), date(2026, 12, 25), date(2027, 2, 8), date(2027, 1, 4)  # 58차: 범위 밖 평일 두 개
    assert not K.session_is_open(at(sat, "11:00")) and not K.session_is_open(at(hol, "11:00"))
    assert K.expected_trading_day(at(sat, "11:00")) == date(2026, 11, 20)
    # 특수 마감이 등록되지 않은 날(미확정·범위 밖 평일)은 15:30 규칙. 2027 연초 개장일 등은 공지 뒤 등록해야 한다
    assert K.close_time(unconfirmed) == K.close_time(outside) == time(15, 30)
    assert K.session_is_open(at(outside, "15:31")) is False


def test_next_and_previous_trading_days_around_csat():
    assert K.next_trading_day(date(2026, 11, 18)) == CSAT
    assert K.next_trading_day(CSAT) == date(2026, 11, 20)
    assert K.previous_trading_day(CSAT) == date(2026, 11, 18)
    assert K.next_trading_day(date(2026, 11, 20)) == date(2026, 11, 23)
    assert K.is_trading_day(CSAT) is True


# ---------- 5. 세션 일정과 기존 달력의 정합성 ----------

def test_session_schedule_and_calendar_agree_on_csat():
    ds, why = ss.resolve(CSAT)
    assert why is None and ds.kind == "override"
    assert ds.status == "announced"  # 실제 11-19 분봉 확인 전까지 data_verified로 올리지 않는다
    assert ds.regular_close == K.close_time(CSAT) == time(16, 30)  # 기존 달력 마감 = 종가 단일가 봉
    assert (ds.regular.start, ds.regular.end) == ("1000", "1620")  # 정규장 마지막 구간 16:20 봉
    assert ds.closing_at == "1630" and ds.late_window is None
    assert (ds.post_close.start, ds.post_close.end) == ("1640", "1659")
    am = ds.after_market_for("stock")
    assert (am.start, am.end) == ("1700", "1959") and ds.after_market_for("etf") is None
    assert ss.calendar_mismatches() == []


def test_default_schedule_matches_regular_close():
    ds, _ = ss.resolve(date(2026, 11, 18))
    assert ds.kind == "default" and ds.regular_close == K.close_time(date(2026, 11, 18)) == time(15, 30)


def test_mismatch_flag_still_appears_if_calendar_loses_the_entry():
    from app import session_bars as sb

    cal = K.calendar()
    unfixed = K.Calendar(**{**cal.__dict__, "special_close": {}})
    assert ss.calendar_mismatches(cal=unfixed) == [{"date": "2026-11-19", "schedule_close": "16:30", "calendar_close": "15:30"}]
    r = sb.classify_day(CSAT, [], "stock", at(CSAT, "21:00"), cal=unfixed)
    assert "calendar_close_mismatch" in r.flags
    r2 = sb.classify_day(CSAT, [], "stock", at(CSAT, "21:00"))
    assert "calendar_close_mismatch" not in r2.flags
