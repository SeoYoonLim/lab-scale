"""app.period_parser 단위 테스트. "오늘"을 2026-10-01(목요일)로 고정해서 날짜 계산을 검증한다."""

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from app.period_parser import _business_days_between, mentions_period, parse_period

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

    def test_sino_korean_number_words_are_not_matched(self):
        # 2026-10-27부터 고유어 수량("한 달", "일주일")은 인식한다(TestNativeQuantity). 한자어 숫자는 여전히 아니다.
        assert parse("삼개월 동안 흐름") is None
        assert parse("하루 만에 급등") is None

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


class TestMentionsPeriod:
    """기본 기간을 채워도 되는지 판단하는 용도: 사용자가 기간(또는 시점)을 말했으면 True."""

    @pytest.mark.parametrize(
        "question",
        [
            # parse_period가 인식하는 표현
            "삼성전자 지난주 주가 흐름", "삼성전자 오늘 주가", "삼성전자 최근 3일 등락률", "삼성전자 3개월 주가 흐름", "올해 흐름",
            # parse_period는 None이지만(값으로 못 바꿈) 기간을 말한 표현
            "SK하이닉스 지난 한 달 주가 어땠어?", "한달 동안 흐름", "일주일 동안 흐름", "두 달 추이", "석 달 흐름",
            "삼성전자 하루 주가 움직임", "며칠간 흐름", "보름 동안 추이", "반년 흐름", "최근 분기 주가 흐름", "주간 흐름",
            "한 주 동안 흐름", "삼성전자 당일 주가 흐름", "장중 흐름",
            # 숫자가 들어간 표현은 기간일 수 있다
            "삼성전자 최근 5거래일 주가 흐름", "10년 전 주가 흐름", "2024년 주가 추이",
        ],
    )
    def test_true(self, question):
        assert mentions_period(question) is True

    @pytest.mark.parametrize(
        "question",
        [
            "삼성전자 최근 주가 흐름 알려줘",
            "삼성전자 최근 상황을 종합해서 알려줘. 주가, 뉴스, 공시를 같이 확인해줘.",
            "현대차 주가 추이 좀 보여줘",
            "카카오 요즘 주가 어때?",
            "005930 주가 흐름 알려줘",  # 6자리 종목코드는 숫자로 치지 않는다
            "신한 주가 흐름", "대한 주식 흐름",  # '한 주'가 아니라 '...한 주가/주식'
            "한 달러에 얼마야?",  # '한 달'이 아니다
            "", None,
        ],
    )
    def test_false(self, question):
        assert mentions_period(question) is False

    def test_unparsed_expressions_are_still_none_for_parse_period(self):
        """mentions_period가 True여도 parse_period가 값으로 바꾸지 못하는 표현이 있다("하루", "며칠", "분기")."""
        for q in ("삼성전자 하루 주가 움직임", "며칠간 흐름", "최근 분기 주가 흐름"):
            assert parse(q) is None and mentions_period(q) is True


class TestNativeQuantity:
    """고유어 수량 표현(2026-10-27 추가). NOW는 2026-10-01(목)."""

    @pytest.mark.parametrize("q, matched, start", [
        ("한 달 동안 흐름", "한 달", date(2026, 9, 1)),
        ("한달 흐름", "한달", date(2026, 9, 1)),
        ("최근 두 달 주가", "두 달", date(2026, 8, 1)),
        ("석 달 추이", "석 달", date(2026, 7, 1)),
        ("일주일 동안 흐름", "일주일", date(2026, 9, 25)),
        ("한 주 동안 흐름", "한 주", date(2026, 9, 25)),
        ("두 주 흐름", "두 주", date(2026, 9, 18)),
        ("반년 흐름", "반년", date(2026, 4, 1)),
        ("반 년 동안", "반 년", date(2026, 4, 1)),
        ("열흘 동안", "열흘", date(2026, 9, 22)),
        ("보름 동안 추이", "보름", date(2026, 9, 17)),
    ])
    def test_native_quantity_ranges(self, q, matched, start):
        r = parse(q)
        assert r is not None and r.matched_text == matched
        assert (r.start_date, r.end_date) == (start, NOW.date())

    def test_one_month_equals_1_gaewol(self):
        assert parse("한 달 흐름").period_days == parse("1개월 흐름").period_days

    @pytest.mark.parametrize("q", ["한 달러에 얼마야?", "신한 주가 흐름", "대한 주식 흐름", "신한지주 한 주식 가격"])
    def test_not_part_of_other_words(self, q):
        assert parse(q) is None

    def test_first_expression_still_wins(self):
        assert parse("지난주랑 한 달 비교").matched_text == "지난주"
