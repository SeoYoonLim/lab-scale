"""stock_tool의 기간 요약(summarize_period) 단위 테스트. DB 없이 가격 목록만으로 확인한다.

기간 요약이 없을 때는 모델이 최상위 change_pct(마지막 하루 등락률)를 기간 전체의 등락률로 옮겼다
("SK하이닉스의 지난 한 달 주가는 1.69% 하락" - 실제로는 하루치 -1.69%, 30거래일로는 +0.90%)."""

import pytest

from app.tools.stock_tool import summarize_period


def price(day, close, pct=None, volume=1000):
    return {"price_date": day, "close_price": float(close), "volume": volume, "change_pct": pct}


PRICES = [
    price("2026-10-02", 110, 10.0),
    price("2026-10-06", 99, -10.0),
    price("2026-10-07", 121, 22.22),
    price("2026-10-08", 105, -13.22, volume=20_157_893),
]
BASE = {"price_date": "2026-10-01", "close_price": 100.0}


class TestSummarizePeriod:
    def test_single_day_has_no_summary(self):
        """하루치 조회는 최상위 change_pct가 곧 답이라 요약을 붙이지 않는다(기존 응답과 완전히 같다)."""
        assert summarize_period("삼성전자", [PRICES[0]], BASE) == {}
        assert summarize_period("삼성전자", [], None) == {}

    def test_change_is_measured_from_the_close_before_the_window(self):
        s = summarize_period("삼성전자", PRICES, BASE)
        assert s["period_start_date"] == "2026-10-01" and s["period_end_date"] == "2026-10-08"
        assert s["trading_days"] == 4  # 직전 종가 대비이므로 조회한 4거래일 전부가 구간이다
        assert s["period_change_pct"] == 5.0  # 100 -> 105
        # 마지막 하루 등락률(-13.22%)과 다른 값이다: 이 둘을 헷갈리지 않게 하는 것이 요약의 목적
        assert s["period_change_pct"] != PRICES[-1]["change_pct"]

    def test_without_a_base_row_uses_the_first_close(self):
        """저장된 주가가 모자라 직전 거래일이 없으면 첫 종가 대비로 계산하고, 거래일 수도 하나 줄인다."""
        s = summarize_period("삼성전자", PRICES, None)
        assert s["period_start_date"] == "2026-10-02" and s["trading_days"] == 3
        assert s["period_change_pct"] == round((105 / 110 - 1) * 100, 2) == -4.55

    def test_summary_sentence_states_period_and_last_day_separately(self):
        text = summarize_period("삼성전자", PRICES, BASE)["summary"]
        assert text == (
            "삼성전자의 2026-10-08 종가는 105원으로, 2026-10-01 종가 100원 대비 4거래일간 +5.00%입니다. "
            "이 기간 종가 최고는 121원(2026-10-07), 최저는 99원(2026-10-06)입니다. "
            "마지막 거래일(2026-10-08) 하루 등락률은 -13.22%, 거래량은 20,157,893주입니다."
        )

    def test_large_prices_are_written_with_thousands_separators(self):
        """모델이 1686000.0 같은 값을 168,600원으로 잘못 옮긴 적이 있어 문장에는 자릿수 구분을 넣는다."""
        prices = [price("2026-10-07", 1_715_000, 1.0), price("2026-10-08", 1_686_000, -1.69)]
        text = summarize_period("SK하이닉스", prices, {"price_date": "2026-10-06", "close_price": 1_671_000.0})["summary"]
        assert "1,686,000원" in text and "1,671,000원" in text and "+0.90%" in text

    def test_missing_last_day_values_are_left_out_of_the_sentence(self):
        prices = [price("2026-10-07", 100), price("2026-10-08", 110, pct=None, volume=None)]
        s = summarize_period("삼성전자", prices, None)
        assert s["period_change_pct"] == 10.0
        assert "마지막 거래일" not in s["summary"]

    def test_only_volume_on_the_last_day(self):
        prices = [price("2026-10-07", 100), price("2026-10-08", 110, pct=None, volume=5)]
        assert summarize_period("삼성전자", prices, None)["summary"].endswith("마지막 거래일(2026-10-08) 하루 거래량은 5주입니다.")

    def test_zero_base_close_gives_no_summary(self):
        assert summarize_period("삼성전자", PRICES, {"price_date": "2026-10-01", "close_price": 0.0}) == {}

    @pytest.mark.parametrize("key", ["period_start_date", "period_end_date", "trading_days", "period_change_pct", "summary"])
    def test_keys(self, key):
        assert key in summarize_period("삼성전자", PRICES, BASE)
