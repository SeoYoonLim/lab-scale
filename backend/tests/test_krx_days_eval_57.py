"""57차: 평가 전용 KRX 거래일 계산(evals/krx_days.py)과 평가기 세 모듈(forecast_check·run_quality·quality_judges)·metrics의 일관성.

순수 함수(DB·네트워크·현재 시각 없음). 제품 달력(app.krx_calendar)은 테스트에서만 비교 기준(오라클)으로 쓴다(평가 모듈은 import하지 않는다).
"""
import ast
import json
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from evals import forecast_check as FC, krx_days as KD, metrics as M, quality_judges as J, run_quality as RQ

D = date.fromisoformat
RAW = json.loads(KD.CALENDAR_FILE.read_text(encoding="utf-8"))
CSAT = D("2026-11-19")


def old_expected_day24(gen: str) -> date:
    """56차까지의 forecast_check._expected_day24(평일만, KRX 휴장일 무시). 변경 전 동작 기록용."""
    t = datetime.strptime(gen, "%Y-%m-%d %H:%M")
    d = t.date()
    if t.weekday() < 5 and t.strftime("%H:%M") < KD.close_hhmm(d):
        d -= timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


# ---------- 날짜 판정 ----------

@pytest.mark.parametrize("d,status", [
    ("2026-11-18", KD.TRADING),   # 일반 평일
    ("2026-11-21", KD.WEEKEND),   # 토
    ("2026-11-22", KD.WEEKEND),   # 일
    ("2026-10-09", KD.HOLIDAY),   # 한글날
    ("2026-02-17", KD.HOLIDAY),   # 설날
    ("2026-11-19", KD.TRADING),   # 특수 거래시간 거래일(수능일)
    ("2027-01-01", KD.HOLIDAY),   # 범위 밖이지만 고정 공휴일
    ("2027-02-08", KD.UNKNOWN),   # 범위 밖 평일
    ("2026-12-31", KD.HOLIDAY),   # 연말 휴장(58차 확정, 57차까지 미확정)
    ("2023-10-27", KD.UNKNOWN),   # 범위 시작 전 평일
])
def test_day_status(d, status):
    assert KD.day_status(D(d)) == status


def test_special_session_day_is_a_normal_trading_day_with_its_own_hours():
    assert KD.is_trading_day(CSAT) is True
    assert (KD.open_hhmm(CSAT), KD.close_hhmm(CSAT)) == ("10:00", "16:30")
    assert (KD.open_hhmm(D("2026-11-18")), KD.close_hhmm(D("2026-11-18"))) == ("09:00", "15:30")


@pytest.mark.parametrize("d", ["2027-02-08", "2027-01-04", "2023-10-27"])
def test_unknown_days_raise_instead_of_guessing(d):
    with pytest.raises(KD.CalendarUnknown) as e:
        KD.is_trading_day(D(d))
    assert e.value.day == D(d) and "krx_calendar.json" in str(e.value)
    with pytest.raises(KD.CalendarUnknown):
        J.krx_closed(D(d))


def test_status_matches_product_calendar_everywhere():
    """평가기(독립 구현)와 제품 달력의 날짜 상태가 2023~2027 모든 날짜에서 같다(같은 데이터 파일)."""
    from app import krx_calendar as K

    d = D("2023-01-01")
    while d <= D("2027-12-31"):
        assert KD.day_status(d) == K.day_status(d), d
        if KD.day_status(d) == KD.TRADING:
            assert KD.close_hhmm(d) == K.close_time(d).strftime("%H:%M")
            assert KD.open_hhmm(d) == K.open_time(d).strftime("%H:%M")
        d += timedelta(days=1)


# ---------- 달력 파일 오류 ----------

def _write(tmp_path, data) -> Path:
    p = tmp_path / "cal.json"
    p.write_text(data if isinstance(data, str) else json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return p


def test_missing_file_and_broken_json(tmp_path):
    with pytest.raises(KD.CalendarError, match="없습니다"):
        KD.load(tmp_path / "nope.json")
    with pytest.raises(KD.CalendarError, match="JSON"):
        KD.load(_write(tmp_path, "{not json"))


def _mut(f):
    data = json.loads(json.dumps(RAW))
    f(data)
    return data


@pytest.mark.parametrize("mutate", [
    lambda d: d["holidays"].append(dict(d["holidays"][0])),                          # 휴장일 중복
    lambda d: d["special_sessions"].append(dict(d["special_sessions"][0])),          # 특수일 중복
    lambda d: d["unconfirmed"].append({"date": d["holidays"][0]["date"]}),           # 미확정 = 휴장일
    lambda d: d["holidays"][0].update(date="2026/10/09"),                            # 날짜 형식
    lambda d: d["holidays"][0].update(date="20261009"),                              # 날짜 형식(구분자 없음)
    lambda d: d["holidays"][0].update(date="2026-11-21"),                            # 주말 휴장일
    lambda d: d["holidays"][0].update(date="2028-01-03"),                            # 범위 밖 휴장일
    lambda d: d["special_sessions"][0].update(close="16:3"),                         # 시각 형식
    lambda d: d["special_sessions"][0].update(close="24:00"),                        # 시각 범위
    lambda d: d["special_sessions"][0].update(open="16:30"),                         # 개장 >= 마감
    lambda d: d["special_sessions"][0].update(date="2026-10-09"),                    # 특수일이 휴장일
    lambda d: d.update(regular_close="15:3"),                                        # 기본 마감 형식
    lambda d: d["fixed_annual"][0].update(md="13-01"),                               # 고정 공휴일 형식
    lambda d: d.pop("coverage"),                                                     # 범위 없음
    lambda d: d["coverage"].update(start="2027-01-01"),                              # 시작 > 끝
])
def test_invalid_calendar_files_are_rejected(tmp_path, mutate):
    with pytest.raises(KD.CalendarError):
        KD.load(_write(tmp_path, _mut(mutate)))


def test_real_file_loads_with_expected_content():
    cal = KD.load()
    assert (cal.start, cal.end) == (D("2023-10-30"), D("2026-12-31"))
    assert len(cal.holidays) == 56 and cal.unconfirmed == frozenset()  # 58차: 12-31 연말 휴장 확정
    assert cal.special_close == {CSAT: "16:30"} and cal.special_open == {CSAT: "10:00"}


@pytest.mark.parametrize("bad", ["16:3", "1630", "24:00", None])
def test_invalid_time_inputs(bad):
    with pytest.raises(ValueError):
        KD.before_close(D("2026-11-18"), bad)
    with pytest.raises(ValueError):
        KD.expected_trading_day(D("2026-11-18"), bad)


# ---------- 거래일 수 ----------

@pytest.mark.parametrize("start,n,want", [
    ("2026-09-21", 5, "2026-09-30"),     # 추석(09-24·25)+주말
    ("2026-09-21", 20, "2026-10-23"),    # 추석·개천절 대체(10-05)·한글날(10-09)
    ("2026-09-21", 60, "2026-12-18"),
    ("2026-11-13", -20, "2026-10-16"),
    ("2026-11-13", -60, "2026-08-14"),   # 광복절 대체(08-17) 포함
    ("2025-10-02", 1, "2025-10-10"),     # 연속 휴장 10-03~10-09(7일)
    ("2025-12-30", 1, "2026-01-02"),     # 연말 휴장·신정
    ("2026-02-13", 1, "2026-02-19"),     # 설 연휴
    ("2026-11-16", 5, "2026-11-23"),     # 수능일은 하루로 센다
])
def test_nth_trading_day(start, n, want):
    assert KD.nth_trading_day(D(start), n) == D(want)


@pytest.mark.parametrize("start,end,want", [
    ("2026-11-16", "2026-11-20", 5),     # 특수 거래일 포함 주
    ("2026-11-14", "2026-11-22", 5),     # 주말 포함
    ("2025-09-29", "2025-10-10", 5),     # 연속 휴장
    ("2025-12-24", "2026-01-05", 6),     # 연말·연초(성탄절·연말 휴장·신정)
    ("2026-02-09", "2026-02-20", 7),     # 설
    ("2026-09-28", "2026-10-05", 5),     # 끝이 휴장일(10-05)
])
def test_count_trading_days(start, end, want):
    assert KD.count_trading_days(D(start), D(end)) == want


def test_counts_match_product_for_5_20_60_windows():
    from app import krx_calendar as K

    d = D("2024-01-02")
    while d <= D("2026-09-30"):
        if KD.day_status(d) == KD.TRADING:
            for n in (5, 20, 60):
                k = d
                for _ in range(n):
                    k = K.next_trading_day(k)
                assert KD.nth_trading_day(d, n) == k
                assert KD.count_trading_days(d + timedelta(days=1), k) == n
        d += timedelta(days=7)


def test_counting_into_unknown_days_raises():
    with pytest.raises(KD.CalendarUnknown):
        KD.nth_trading_day(D("2026-12-30"), 1)         # 12-31 휴장·01-01 신정 뒤 2027-01-04 미확인(58차)
    with pytest.raises(KD.CalendarUnknown):
        KD.count_trading_days(D("2026-12-28"), D("2027-01-08"))
    with pytest.raises(KD.CalendarUnknown):
        M._business_days_between(D("2026-12-30"), D("2027-01-05"))


# ---------- 경계 조건 ----------

@pytest.mark.parametrize("d,hhmm,want", [
    ("2026-11-21", "12:00", "2026-11-20"),   # 토: 직전 금
    ("2026-11-23", "08:00", "2026-11-20"),   # 거래일 직전 주말 뒤 월요일 개장 전
    ("2026-10-05", "12:00", "2026-10-02"),   # 휴장일 당일
    ("2026-10-06", "10:00", "2026-10-02"),   # 휴장일 직후 거래일 장중
    ("2026-10-06", "15:30", "2026-10-06"),
    ("2025-10-09", "12:00", "2025-10-02"),   # 연속 휴장 마지막 날
    ("2025-10-10", "09:30", "2025-10-02"),   # 연속 휴장 뒤 첫 거래일 장중
    ("2026-11-19", "15:30", "2026-11-18"),   # 특수 마감일 15:30~16:30은 장중
    ("2026-11-19", "16:29", "2026-11-18"),
    ("2026-11-19", "16:30", "2026-11-19"),
    ("2026-11-18", "15:29", "2026-11-17"),   # 일반 거래일 15:30 기준
    ("2026-11-18", "15:30", "2026-11-18"),
])
def test_expected_trading_day_boundaries(d, hhmm, want):
    assert KD.expected_trading_day(D(d), hhmm) == D(want)
    assert J.expected_trading_day(D(d), hhmm) == D(want)
    assert FC._expected_day24(f"{d} {hhmm}") == D(want)


def test_period_ending_on_holiday_excludes_it():
    assert KD.trading_days(D("2026-09-28"), D("2026-10-05"))[-1] == D("2026-10-02")
    assert KD.trading_days_after(D("2026-10-02"), D("2026-10-05")) == 0
    assert KD.trading_days_after(D("2026-10-02"), D("2026-10-06")) == 1


def test_before_close_is_false_on_holidays_and_weekends():
    assert KD.before_close(D("2026-10-05"), "10:00") is False     # 휴장일(평일)
    assert KD.before_close(D("2026-11-21"), "10:00") is False     # 토
    assert KD.before_close(CSAT, "16:29") is True and KD.before_close(CSAT, "16:30") is False
    assert FC._before_close24("2026-10-05 10:00") is False        # 56차까지 True(평일이라)
    assert FC._before_close24("2026-11-19 16:00") is True


# ---------- forecast_check 변경 전후 ----------

@pytest.mark.parametrize("gen,old,new", [
    ("2026-10-05 20:00", "2026-10-05", "2026-10-02"),   # 휴장일 당일 저녁: 휴장일을 기대 거래일로 잡던 것
    ("2026-10-09 18:00", "2026-10-09", "2026-10-08"),   # 한글날(금) 저녁
    ("2026-10-06 10:00", "2026-10-05", "2026-10-02"),   # 휴장일 다음 날 장중
    ("2026-10-12 10:00", "2026-10-09", "2026-10-08"),   # 한글날(금) 다음 월 장중
    ("2026-09-28 10:00", "2026-09-25", "2026-09-23"),   # 추석 연휴 뒤
    ("2026-02-19 10:00", "2026-02-18", "2026-02-13"),   # 설 연휴 뒤
])
def test_forecast_expected_day_fixes_holidays(gen, old, new):
    assert old_expected_day24(gen) == D(old)
    assert FC._expected_day24(gen) == D(new)


def test_forecast_expected_day_unchanged_away_from_holidays():
    """휴장일이 끼지 않은 날은 56차와 결과가 같다(2026-10-13~11-30, 하루 6개 시각)."""
    d = D("2026-10-13")
    while d <= D("2026-11-30"):
        for hhmm in ("00:30", "09:00", "15:29", "15:30", "16:29", "16:30", "20:00"):
            gen = f"{d.isoformat()} {hhmm}"
            assert FC._expected_day24(gen) == old_expected_day24(gen), gen
        d += timedelta(days=1)


# ---------- 모듈 간 일관성 ----------

def _grid():
    d = D("2025-09-01")
    while d <= D("2026-12-30"):
        for hhmm in ("08:00", "10:00", "15:29", "15:30", "16:29", "16:30", "21:00"):
            yield d, hhmm
        d += timedelta(days=1)


def test_three_evaluators_and_metrics_agree_with_each_other_and_product():
    from app import krx_calendar as K

    for d, hhmm in _grid():
        want = KD.expected_trading_day(d, hhmm)
        assert J.expected_trading_day(d, hhmm) == want
        assert FC._expected_day24(f"{d.isoformat()} {hhmm}") == want
        now = datetime.combine(d, datetime.strptime(hhmm, "%H:%M").time()).replace(tzinfo=K.KST)
        assert K.expected_trading_day(now) == want, (d, hhmm)  # 제품과도 같다(범위 안)
        rows = [(d - timedelta(days=1), 1.0), (d, 2.0)]
        dropped = RQ._visible_rows(rows, now) == rows[:1]
        assert dropped is KD.before_close(d, hhmm) is FC._before_close24(f"{d.isoformat()} {hhmm}") is K.session_is_open(now)
    for a in (D("2025-09-30"), D("2025-12-24"), D("2026-02-12"), D("2026-09-23"), D("2026-11-13")):
        for k in range(0, 40, 3):
            b = a + timedelta(days=k)
            assert M._business_days_between(a, b) == J.trading_days_behind(a, b) == KD.trading_days_after(a, b) == K.lag(a, b).days


def test_metrics_staleness_now_uses_trading_days():
    # 연휴 앞 금요일 데이터, 연휴 뒤 첫 거래일: 평일 수 2(stale), KRX 거래일 수 1(not stale, 제품 data_status와 같다)
    last, today = D("2026-10-02"), D("2026-10-06")
    assert M._business_days_between(last, today) == 1
    assert M.data_date_ok("2026-10-02 종가입니다", last, today) is True


def test_evaluator_modules_do_not_import_product_code():
    for mod in (KD, J):
        tree = ast.parse(Path(mod.__file__).read_text(encoding="utf-8"))
        names = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)} | \
                {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        assert not any(m and (m == "app" or m.startswith("app.")) for m in names), mod.__name__
