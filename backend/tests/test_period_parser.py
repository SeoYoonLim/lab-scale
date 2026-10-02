"""app.period_parser 단위 테스트. "오늘"을 2026-10-01(목요일)로 고정해서 날짜 계산을 검증한다."""

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from app.period_parser import _business_days_between, parse_period

KST = ZoneInfo("Asia/Seoul")
NOW = datetime(2026, 10, 1, 12, 0, tzinfo=KST)  # 목요일, 또한 월의 첫째 날


def parse(question: str):
    return parse_period(question, now=NOW)


class TestBusinessDaysBetween:
    @pytest.mark.parametrize(
        "start, end, expected",
        [
            (date(2026, 10, 1), date(2026, 10, 1), 1),  # 목요일 하루
            (date(2026, 9, 26), date(2026, 9, 26), 0),  # 토요일 하루 (평일 0일)
            (date(2026, 9, 21), date(2026, 9, 27), 5),  # 월~일 한 주 (평일 5일)
            (date(2026, 9, 1), date(2026, 9, 30), 22),  # 9월(화요일 시작) 전체
            (date(2026, 9, 27), date(2026, 9, 21), 5),  # 순서가 뒤집혀도 같은 결과
        ],
    )
    def test_counts_weekdays_only(self, start, end, expected):
        assert _business_days_between(start, end) == expected


class TestFixedRelativePhrases:
    def test_today(self):
        r = parse("오늘 주가 알려줘")
        assert (r.start_date, r.end_date, r.period_days) == (date(2026, 10, 1), date(2026, 10, 1), 1)

    def test_yesterday(self):
        r = parse("어제 거래량 얼마였어")
        assert (r.start_date, r.end_date, r.period_days) == (date(2026, 9, 30), date(2026, 9, 30), 1)

    @pytest.mark.parametrize("phrase", ["이번주", "이번 주"])
    def test_this_week_both_spacings(self, phrase):
        r = parse(f"{phrase} 등락률 알려줘")
        assert (r.start_date, r.end_date) == (date(2026, 9, 28), date(2026, 10, 1))  # 월요일~오늘(목)
        assert r.period_days == 4

    @pytest.mark.parametrize("phrase", ["지난주", "지난 주"])
    def test_last_week_both_spacings(self, phrase):
        r = parse(f"{phrase} 주가 흐름")
        assert (r.start_date, r.end_date) == (date(2026, 9, 21), date(2026, 9, 27))  # 지난 월~일
        assert r.period_days == 5

    @pytest.mark.parametrize("phrase", ["이번달", "이번 달"])
    def test_this_month_both_spacings(self, phrase):
        # NOW가 10월 1일(이번 달 첫날)이라 범위가 하루뿐이다.
        r = parse(f"{phrase} 주가")
        assert (r.start_date, r.end_date, r.period_days) == (date(2026, 10, 1), date(2026, 10, 1), 1)

    @pytest.mark.parametrize("phrase", ["지난달", "지난 달"])
    def test_last_month_both_spacings(self, phrase):
        r = parse(f"{phrase} 등락률")
        assert (r.start_date, r.end_date) == (date(2026, 9, 1), date(2026, 9, 30))
        assert r.period_days == 22

    def test_this_year(self):
        r = parse("올해 누적 수익률")
        assert (r.start_date, r.end_date) == (date(2026, 1, 1), date(2026, 10, 1))

    def test_last_year(self):
        r = parse("작년 실적")
        assert (r.start_date, r.end_date) == (date(2025, 1, 1), date(2025, 12, 31))

    def test_jijinan_dal_the_month_before_last_is_not_matched_as_last_month(self):
        assert parse("지지난달 등락률") is None

    def test_jijinan_ju_the_week_before_last_is_not_matched_as_last_week(self):
        assert parse("지지난주 등락률") is None

    def test_jaejaknyeon_the_year_before_last_is_not_matched_as_last_year(self):
        assert parse("재작년 실적") is None


class TestQuantityExpressions:
    def test_days(self):
        r = parse("3일 동안 주가")
        assert (r.start_date, r.end_date, r.period_days) == (date(2026, 9, 29), date(2026, 10, 1), 3)

    def test_weeks(self):
        r = parse("2주간 등락률")
        assert (r.start_date, r.end_date, r.period_days) == (date(2026, 9, 18), date(2026, 10, 1), 10)

    def test_months_uses_calendar_subtraction(self):
        r = parse("3개월 동안 주가 알려줘")
        assert (r.start_date, r.end_date) == (date(2026, 7, 1), date(2026, 10, 1))

    def test_years(self):
        r = parse("1년 수익률")
        assert (r.start_date, r.end_date) == (date(2025, 10, 1), date(2026, 10, 1))

    def test_month_end_overflow_clamps_to_last_day_of_target_month(self):
        # 10/31에서 3개월 전 = 7/31이지만, 2개월 전처럼 짧은 달로 가면 그 달의 마지막 날로 clamp된다.
        now = datetime(2026, 10, 31, tzinfo=KST)
        r = parse_period("1개월 동안 주가", now=now)
        assert r.start_date == date(2026, 9, 30)  # 9월은 31일이 없음

    @pytest.mark.parametrize("quantity, unit", [(0, "일"), (0, "주"), (3651, "일"), (121, "개월"), (51, "년")])
    def test_out_of_range_quantity_is_not_matched(self, quantity, unit):
        assert parse(f"{quantity}{unit} 동안 주가") is None

    def test_native_korean_number_words_are_not_matched(self):
        assert parse("일주일 동안 흐름") is None
        assert parse("한 달 동안 흐름") is None

    def test_point_in_time_expression_with_jeon_is_not_matched(self):
        # "N년 전"은 기간(범위)이 아니라 특정 시점을 가리키므로 인식하지 않는다.
        assert parse("10년 전 상장했어?") is None
        assert parse("3일 전에 공시 났어?") is None

    def test_large_absolute_year_is_not_matched_as_a_duration(self):
        assert parse("2024년 몇월에 상장했어") is None


class TestNoMatchFallback:
    def test_question_without_any_period_expression_returns_none(self):
        assert parse("삼성전자 주가 알려줘") is None

    def test_blank_question_returns_none(self):
        assert parse("") is None
        assert parse_period(None, now=NOW) is None


class TestFirstMatchWins:
    def test_earliest_expression_in_the_question_is_used(self):
        r = parse("지난주랑 비교해서 최근 3일은 어때?")
        assert r.matched_text == "지난주"
        assert (r.start_date, r.end_date) == (date(2026, 9, 21), date(2026, 9, 27))

    def test_order_reversed_picks_the_other_one(self):
        r = parse("최근 3일이랑 지난주를 비교해줘")
        assert r.matched_text == "3일"
