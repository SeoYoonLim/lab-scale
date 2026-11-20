"""58차: 2026년 연말(12-25 성탄절·12-30 폐장·12-31 연말 휴장)과 2027년 미확정 날짜. 순수 함수(DB·네트워크·현재 시각 없음).

근거(docs/KRX_CALENDAR_YEAR_END_UPDATE_58.md 3절): KRX 규정 "연말 휴장일 12월 31일(공휴일·토요일이면 직전 매매거래일)",
KIND 2026 코스닥시장 공시일정 캘린더 12월 "2026년 증권시장 폐장"·"2026년 증권시장 연말휴장", 관공서의 공휴일에 관한 규정 제2조(1월 1일·12월 25일).
2027년은 KRX 공식 휴장일 공지가 아직 없다: 신정(고정 공휴일 규칙)만 휴장이고 나머지 평일은 확정하지 않는다.
"""
import copy
import json
from datetime import date, datetime, timedelta

import pytest

from app import krx_calendar as K
from evals import forecast_check as FC, krx_days as KD, metrics as M, quality_judges as J, run_quality as RQ

D = date.fromisoformat
RAW = json.loads(K.DATA_PATH.read_text(encoding="utf-8"))


# ---------- 달력 파일 ----------

def test_year_end_entries_in_file():
    hol = {h["date"]: h for h in RAW["holidays"]}
    assert hol["2026-12-31"] == {"date": "2026-12-31", "name": "연말 휴장", "source": "krx_rule"}
    assert hol["2026-12-25"]["source"] == "statute"
    assert "krx_rule" in RAW["sources"] and "12월 31일" in RAW["sources"]["krx_rule"]
    assert RAW["unconfirmed"] == []
    assert [h["date"] for h in RAW["holidays"]].count("2026-12-31") == 1
    assert RAW["coverage"] == {"start": "2023-10-30", "end": "2026-12-31"}  # 2027로 늘리지 않았다
    assert not any(h["date"] >= "2027" for h in RAW["holidays"])  # 2027 날짜를 등록하지 않았다


def test_year_end_rule_matches_past_years():
    """연말 휴장 규칙(12-31, 토·공휴일이면 직전 거래일)이 달력의 과거 연말과 같다: 2023-12-29(12-31 일요일), 2024·2025-12-31."""
    for y, want in ((2023, "2023-12-29"), (2024, "2024-12-31"), (2025, "2025-12-31"), (2026, "2026-12-31")):
        d = date(y, 12, 31)
        while d.weekday() >= 5:
            d -= timedelta(days=1)
        assert d == D(want) and K.day_status(d) == K.HOLIDAY and KD.day_status(d) == KD.HOLIDAY


# ---------- 2026년 연말 판정(제품 달력·평가 유틸리티) ----------

@pytest.mark.parametrize("d,status", [
    ("2026-12-24", "trading"), ("2026-12-25", "holiday"), ("2026-12-28", "trading"), ("2026-12-29", "trading"),
    ("2026-12-30", "trading"), ("2026-12-31", "holiday"), ("2027-01-01", "holiday"),
    ("2027-01-02", "weekend"), ("2027-01-04", "unknown"), ("2027-02-08", "unknown"),
])
def test_year_end_day_status(d, status):
    assert K.day_status(D(d)) == status
    assert KD.day_status(D(d)) == status


def test_last_trading_day_of_2026_is_dec_30():
    assert KD.last_trading_day(D("2026-12-31")) == D("2026-12-30")
    assert KD.previous_trading_day(D("2026-12-31")) == D("2026-12-30")
    assert KD.last_trading_day(D("2027-01-03")) == D("2026-12-30")  # 신정·주말을 지나 거꾸로 찾을 때는 확정된 날만 지난다
    assert KD.trading_days(D("2026-12-21"), D("2026-12-31")) == [D(x) for x in (
        "2026-12-21", "2026-12-22", "2026-12-23", "2026-12-24", "2026-12-28", "2026-12-29", "2026-12-30")]
    assert K.previous_trading_day(D("2027-01-01")) == D("2026-12-30")


def test_next_trading_day_after_dec_30_is_not_guessed():
    """12-30 다음 거래일: 12-31 휴장·01-01 신정·주말 뒤 2027-01-04는 KRX 공지 전이라 평가기는 고르지 않고 멈춘다."""
    with pytest.raises(KD.CalendarUnknown) as e:
        KD.next_trading_day(D("2026-12-30"))
    assert e.value.day == D("2027-01-04")
    with pytest.raises(KD.CalendarUnknown):
        KD.nth_trading_day(D("2026-12-30"), 1)
    # 제품은 화면 답을 위해 미확인 평일을 "열릴 수 있는 날"로 센다(36차 정책, 바꾸지 않음). 평가기만 멈춘다
    assert K.next_trading_day(D("2026-12-30")) == D("2027-01-04") and K.day_status(D("2027-01-04")) == K.UNKNOWN


@pytest.mark.parametrize("d,hhmm,want", [
    ("2026-12-24", "15:29", "2026-12-23"), ("2026-12-24", "15:30", "2026-12-24"),
    ("2026-12-25", "12:00", "2026-12-24"),  # 성탄절: 거래일이 아니다
    ("2026-12-28", "10:00", "2026-12-24"),  # 성탄절·주말 뒤 월요일 장중
    ("2026-12-29", "20:00", "2026-12-29"),
    ("2026-12-30", "15:29", "2026-12-29"), ("2026-12-30", "15:30", "2026-12-30"),  # 마지막 거래일
    ("2026-12-31", "12:00", "2026-12-30"), ("2026-12-31", "20:00", "2026-12-30"),  # 연말 휴장
    ("2027-01-01", "12:00", "2026-12-30"),  # 신정
    ("2027-01-03", "20:00", "2026-12-30"),  # 주말
])
def test_expected_day_year_end_all_modules_agree(d, hhmm, want):
    day, w = D(d), D(want)
    assert KD.expected_trading_day(day, hhmm) == w
    assert J.expected_trading_day(day, hhmm) == w
    assert FC._expected_day24(f"{d} {hhmm}") == w
    now = datetime.combine(day, datetime.strptime(hhmm, "%H:%M").time()).replace(tzinfo=K.KST)
    assert K.expected_trading_day(now) == w  # 제품과도 같다
    rows = [(day - timedelta(days=1), 1.0), (day, 2.0)]
    assert (RQ._visible_rows(rows, now) == rows[:1]) is KD.before_close(day, hhmm) is K.session_is_open(now)


def test_year_end_lag_counts_agree():
    for a, b, n in (("2026-12-24", "2026-12-28", 1), ("2026-12-24", "2026-12-31", 3), ("2026-12-29", "2027-01-01", 1),
                    ("2026-12-23", "2026-12-30", 4)):
        assert KD.trading_days_after(D(a), D(b)) == J.trading_days_behind(D(a), D(b)) == M._business_days_between(D(a), D(b)) == n
        assert K.lag(D(a), D(b)).days == n


# ---------- 2027년 미확정 ----------

@pytest.mark.parametrize("d", ["2027-01-04", "2027-02-08", "2027-12-30"])
def test_2027_weekdays_stop_the_evaluation(d):
    with pytest.raises(KD.CalendarUnknown):
        KD.is_trading_day(D(d))
    with pytest.raises(KD.CalendarUnknown):
        J.expected_trading_day(D(d), "20:00")
    with pytest.raises(KD.CalendarUnknown):
        FC._expected_day24(f"{d} 20:00")
    with pytest.raises(KD.CalendarUnknown):
        RQ._visible_rows([(D(d), 1.0)], datetime.combine(D(d), datetime.min.time()).replace(hour=10, tzinfo=K.KST))
    with pytest.raises(KD.CalendarUnknown):
        M._business_days_between(D("2026-12-30"), D(d))


def test_2027_new_year_is_holiday_by_fixed_rule_only():
    """2027-01-01은 고정 공휴일 규칙(fixed_annual 01-01, 관공서의 공휴일에 관한 규정 제2조 제3호)으로만 휴장이다. 휴장일 목록에 넣지 않았다."""
    assert KD.day_status(D("2027-01-01")) == KD.HOLIDAY and K.holiday_name(D("2027-01-01")) == "신정"
    assert ("01-01" in {f["md"] for f in RAW["fixed_annual"]})
    # 설·대체공휴일·연말 휴장은 해마다 달라 범위 밖에서는 확정하지 않는다(규칙으로 만들지 않는다)
    for d in ("2027-02-05", "2027-02-08", "2027-12-31"):
        assert KD.day_status(D(d)) == KD.UNKNOWN


# ---------- 미확정(unconfirmed) 동작은 그대로(합성 달력) ----------

def _with_unconfirmed(day: str):
    data = copy.deepcopy(RAW)
    data["holidays"] = [h for h in data["holidays"] if h["date"] != day]
    data["unconfirmed"] = [{"date": day, "name": "시험용 미확정", "reason": "test"}]
    return data


def test_unconfirmed_mechanism_still_works_with_synthetic_entry():
    data = _with_unconfirmed("2026-12-31")
    cal_p, cal_e = K.parse(data), KD.parse(data)
    d = D("2026-12-31")
    assert K.day_status(d, cal_p) == K.UNKNOWN and "미확정" in K.holiday_name(d, cal_p)
    assert KD.day_status(d, cal_e) == KD.UNKNOWN
    with pytest.raises(KD.CalendarUnknown, match="미확정"):
        KD.expected_trading_day(d, "20:00", cal_e)
    assert KD.expected_trading_day(D("2026-12-30"), "20:00", cal_e) == D("2026-12-30")  # 미확정일을 만나지 않는 계산은 된다
