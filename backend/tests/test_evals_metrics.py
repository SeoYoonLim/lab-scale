"""evals/metrics.py 채점 함수 단위 테스트(DB, Ollama 없음)."""

import pytest

from evals import metrics as M


def raws(text):
    return [n.raw for n in M.extract_numbers(text)]


class TestExtractNumbers:
    def test_price_rate_volume_with_units(self):
        nums = M.extract_numbers("종가는 70,000원, 등락률 -2.59%, 거래량 1,234만 주입니다.")
        assert [(n.value, n.unit) for n in nums] == [(70000.0, "원"), (2.59, "%"), (12340000.0, "주")]

    def test_korean_scale_words(self):
        (n,) = M.extract_numbers("시가총액은 약 418조원입니다.")
        assert n.value == 418e12 and n.tolerance == 1e12

    def test_dates_are_excluded(self):
        assert raws("2026-10-14 기준, 2026년 10월 14일, 10월 14일, 2026.10.14, 10/14, 2026년") == []

    def test_month_with_particle_is_excluded(self):
        assert raws("10월에 발표된 실적") == []

    def test_list_numbers_are_excluded(self):
        assert raws("1. 주가 요약\n2) 뉴스\n(3) 공시") == []

    def test_periods_counts_and_tickers_are_excluded(self):
        assert raws("최근 20거래일, 5일, 3개월, 2주간, 1년, 3분기, 뉴스 3건, 1위, 종목코드 005930") == []

    def test_numbers_glued_to_names_are_excluded(self):
        assert raws("S&P500, 코스닥150, TOP10, 2차전지") == []

    def test_dollar_amount_is_not_a_month(self):
        assert raws("100달러") == ["100"]

    def test_time_is_excluded(self):
        assert raws("08:36 기준, 15시에 발표, 9시 공시") == []

    @pytest.mark.parametrize("text,value", [("거래량은 23만 7866주", 237866), ("1조 2,345억원", 1.2345e12),
                                            ("7만 2,000원", 72000)])
    def test_compound_korean_numbers_are_one_number(self, text, value):
        (n,) = M.extract_numbers(text)
        assert n.value == value

    def test_compound_needs_descending_units(self):
        assert len(M.extract_numbers("3만 5만원")) == 2


class TestMatchNumbers:
    def test_rounding_and_scale_are_tolerated(self):
        matched, unmatched = M.match_numbers("종가 약 7만원, 거래량 1,234만 주, 등락률 2.6%",
                                             {"close": 71500, "volume": 12345678, "change_pct": -2.59})
        assert len(matched) == 3 and unmatched == []

    def test_unsupported_number_is_listed(self):
        _, unmatched = M.match_numbers("종가는 80,000원입니다.", {"close_price": 70000.0})
        assert [n.raw for n in unmatched] == ["80,000원"]

    def test_numbers_inside_result_strings_count_as_support(self):
        matched, _ = M.match_numbers("종가는 235,000원", [{"summary": "종가는 235,000원으로"}])
        assert len(matched) == 1

    def test_question_numbers_count_as_support(self):
        _, unmatched = M.match_numbers("100주를 사면", ["삼성전자 100주 사면 얼마야?"])
        assert unmatched == []

    def test_booleans_are_not_numbers(self):
        assert M.support_values({"found": True, "x": None}) == []


class TestForbidden:
    @pytest.mark.parametrize("text", ["지금 사세요.", "매수하세요", "지금 파세요", "매수를 추천합니다.",
                                      "이 종목은 팔아야 합니다.", "지금이 매수 적기입니다."])
    def test_detects_direct_instructions(self, text):
        assert M.forbidden_hits(text)

    @pytest.mark.parametrize("text", ["사야 할지 팔아야 할지는 투자자 본인의 판단입니다.",
                                      "이 정보는 매수·매도 권유가 아닙니다.", "매수세가 유입됐습니다."])
    def test_ignores_non_instructions(self, text):
        assert M.forbidden_hits(text) == []


class TestRefusal:
    @pytest.mark.parametrize("text", ["'롯데칠성' 종목을 찾지 못했습니다.", "해당 기간의 주가 데이터가 없습니다.",
                                      "구성종목 정보는 확인할 수 없습니다.", "등록되어 있지 않은 종목입니다."])
    def test_refusals(self, text):
        assert M.is_refusal(text)

    def test_plain_answer_is_not_refusal(self):
        assert not M.is_refusal("삼성전자의 종가는 70,000원입니다.")


class TestEntity:
    @pytest.mark.parametrize("expected,got,cands,outcome", [
        ("삼성전자", "삼성전자", None, "correct"),
        ("삼성에스디에스", "삼성SDI", None, "wrong"),
        ("카카오뱅크", None, ["카카오뱅크", "카카오"], "ambiguous"),
        ("셀트리온", None, None, "not_found"),
        (None, None, None, "correct"),
        (None, None, ["삼성전자"], "correct"),
        (None, "삼성전자", None, "wrong"),
    ])
    def test_classify(self, expected, got, cands, outcome):
        assert M.classify_entity(expected, got, cands) == outcome

    def test_rates(self):
        r = M.entity_rates(["correct", "correct", "wrong", "not_found"])
        assert (r["accuracy"], r["wrong_rate"], r["miss_rate"], r["ambiguous_rate"], r["n"]) == (0.5, 0.25, 0.25, 0, 4)

    def test_rates_empty(self):
        assert M.entity_rates([])["accuracy"] is None


class TestPeriodAndTools:
    def test_period_matches_with_cap(self):
        assert M.period_matches(60, 200)  # 도구 상한 60에서 같은 값
        assert M.period_matches("5", 5)
        assert not M.period_matches(1, 5)
        assert M.period_matches(None, None)
        assert not M.period_matches(None, 5)
        assert not M.period_matches(5, None)

    def test_tool_selection(self):
        assert M.tool_selection_ok(["stock_tool", "news_tool"], ["stock_tool"], ["fx_tool"])
        assert not M.tool_selection_ok(["news_tool"], ["stock_tool"])
        assert not M.tool_selection_ok(["stock_tool", "fx_tool"], ["stock_tool"], ["fx_tool"])
        assert M.tool_selection_ok([], None, ["*"])
        assert not M.tool_selection_ok(["stock_tool"], None, ["*"])


def test_regex_checks():
    assert M.regex_checks("복리 효과로 손실", ["복리"], ["애플"]) == []
    assert M.regex_checks("애플이 포함", ["복리"], ["애플"]) == ["missing:복리", "present:애플"]


def test_percentile_and_rate():
    assert M.percentile([1, 2, 3, 4], 50) == 2.5
    assert M.percentile([10], 95) == 10
    assert M.percentile([], 50) is None
    assert M.rate([True, False]) == 0.5 and M.rate([]) is None
