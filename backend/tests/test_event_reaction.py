"""app/event_reaction.py(순수 함수): 기준일 결정, N일 수익률, 지수/초과수익률, 거래량, 가격 조정 경고, 다른 사건 수, 요약."""

import copy
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from app.event_reaction import (
    Bar,
    compute_reaction,
    count_other_events,
    resolve_base_and_start,
    summarize,
    window_end_date,
)

KST = ZoneInfo("Asia/Seoul")
HOLIDAY = date(2026, 10, 9)  # 금요일 휴장(한글날)


def _calendar(start=date(2026, 9, 1), n=45):
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5 and d != HOLIDAY:
            out.append(d)
        d += timedelta(days=1)
    return out


def _bars(n=45, base=100.0, vol=1000.0, start=date(2026, 9, 1)):
    """종가 base, base+1, ... (일정하게 +1), 거래량 vol."""
    return [Bar(d, base + i, vol) for i, d in enumerate(_calendar(start, n))]


def _at(y, m, d, hh, mm):
    return datetime(y, m, d, hh, mm, tzinfo=KST)


DATES = _calendar()


class TestBaseAndStart:
    def test_intraday_news_uses_previous_close_and_same_day_start(self):
        assert resolve_base_and_start(DATES, _at(2026, 10, 7, 10, 0), None) == (date(2026, 10, 6), date(2026, 10, 7))

    def test_before_open_news(self):
        assert resolve_base_and_start(DATES, _at(2026, 10, 7, 8, 0), None) == (date(2026, 10, 6), date(2026, 10, 7))

    def test_after_close_news_uses_same_day_close_and_next_day_start(self):
        assert resolve_base_and_start(DATES, _at(2026, 10, 7, 16, 0), None) == (date(2026, 10, 7), date(2026, 10, 8))

    def test_close_boundary(self):
        assert resolve_base_and_start(DATES, _at(2026, 10, 7, 15, 29), None)[1] == date(2026, 10, 7)
        assert resolve_base_and_start(DATES, _at(2026, 10, 7, 15, 30), None)[1] == date(2026, 10, 8)

    def test_utc_timestamp_is_converted_to_kst(self):
        utc = datetime(2026, 10, 7, 7, 0, tzinfo=timezone.utc)  # 16:00 KST
        assert resolve_base_and_start(DATES, utc, None) == (date(2026, 10, 7), date(2026, 10, 8))
        utc_early = datetime(2026, 10, 6, 15, 30, tzinfo=timezone.utc)  # 10/7 00:30 KST
        assert resolve_base_and_start(DATES, utc_early, None) == (date(2026, 10, 6), date(2026, 10, 7))

    def test_after_close_across_holiday_and_weekend(self):
        # 10/8(목) 장 마감 후: 다음 거래일은 휴장(10/9)과 주말을 건너뛴 10/12
        assert resolve_base_and_start(DATES, _at(2026, 10, 8, 18, 0), None) == (date(2026, 10, 8), date(2026, 10, 12))

    @pytest.mark.parametrize("day", [date(2026, 10, 9), date(2026, 10, 10), date(2026, 10, 11)])
    def test_holiday_or_weekend_news(self, day):
        at = datetime(day.year, day.month, day.day, 11, 0, tzinfo=KST)
        assert resolve_base_and_start(DATES, at, None) == (date(2026, 10, 8), date(2026, 10, 12))

    def test_date_only_on_trading_day(self):
        assert resolve_base_and_start(DATES, None, date(2026, 10, 7)) == (date(2026, 10, 6), date(2026, 10, 7))

    def test_date_only_on_holiday_goes_to_next_trading_day(self):
        assert resolve_base_and_start(DATES, None, HOLIDAY) == (date(2026, 10, 8), date(2026, 10, 12))

    def test_event_before_price_data_has_no_base(self):
        assert resolve_base_and_start(DATES, None, date(2026, 9, 1)) == (None, date(2026, 9, 1))
        assert resolve_base_and_start(DATES, None, date(2026, 8, 1)) == (None, date(2026, 9, 1))

    def test_event_after_last_day_has_no_start(self):
        last = DATES[-1]
        assert resolve_base_and_start(DATES, _at(last.year, last.month, last.day, 16, 0), None) == (last, None)
        far = last + timedelta(days=10)
        assert resolve_base_and_start(DATES, None, far) == (last, None)


class TestReturns:
    def test_returns_count_start_day_as_day_one(self):
        bars = _bars(n=60)
        # 날짜만: 기준 = 10/6 종가, 시작 = 10/7. bars는 +1씩 증가.
        r = compute_reaction(bars, event_date=date(2026, 10, 7))
        by = {b.date: b.close for b in bars}
        base = by[date(2026, 10, 6)]
        ds = [b.date for b in bars]
        i = ds.index(date(2026, 10, 7))
        for n, key in ((1, "d1"), (5, "d5"), (20, "d20")):
            expected = round((bars[i + n - 1].close / base - 1) * 100, 2)
            assert r["returns"][key]["pct"] == expected and r["returns"][key]["available"] is True
        assert r["base_date"] == date(2026, 10, 6) and r["base_close"] == base
        assert r["reaction_start_date"] == date(2026, 10, 7) and r["time_known"] is False

    def test_time_known_flag(self):
        assert compute_reaction(_bars(), event_at=_at(2026, 10, 7, 10, 0))["time_known"] is True

    def test_not_enough_days_gives_null_with_reason(self):
        bars = _bars()
        last = bars[-1].date
        r = compute_reaction(bars, event_date=last - timedelta(days=3))
        assert r["returns"]["d20"]["pct"] is None
        assert r["returns"]["d20"]["available"] is False
        assert r["returns"]["d20"]["reason"] == "20거래일 경과 전"
        assert r["returns"]["d1"]["available"] is True

    def test_exactly_n_days_available(self):
        bars = _bars()
        ds = [b.date for b in bars]
        start = ds[len(ds) - 20]  # 시작일부터 마지막 날까지 정확히 20거래일
        r = compute_reaction(bars, event_date=start)
        assert r["returns"]["d20"]["available"] is True
        r2 = compute_reaction(bars, event_date=ds[len(ds) - 19])
        assert r2["returns"]["d20"]["available"] is False

    def test_no_base_gives_all_unavailable(self):
        r = compute_reaction(_bars(), event_date=date(2026, 8, 1))
        assert r["base_close"] is None and r["volume_ratio"] is None and r["volume_sample_days"] == 0
        for k in ("d1", "d5", "d20"):
            assert r["returns"][k]["available"] is False and "기준 종가 없음" in r["returns"][k]["reason"]

    def test_no_start_day_yet(self):
        bars = _bars()
        last = bars[-1].date
        r = compute_reaction(bars, event_at=_at(last.year, last.month, last.day, 17, 0))
        assert r["reaction_start_date"] is None and r["base_date"] == last
        assert r["returns"]["d1"]["available"] is False

    def test_empty_prices(self):
        r = compute_reaction([], event_date=date(2026, 10, 7))
        assert r["base_close"] is None and r["returns"]["d1"]["available"] is False

    def test_requires_event_time_or_date(self):
        with pytest.raises(ValueError):
            compute_reaction(_bars())

    def test_rounding_two_decimals(self):
        bars = [Bar(date(2026, 10, 5), 300.0, 10), Bar(date(2026, 10, 6), 301.0, 10)]
        r = compute_reaction(bars, event_date=date(2026, 10, 6))
        assert r["returns"]["d1"]["pct"] == 0.33  # 0.3333..


class TestIndex:
    def _idx(self, bars, f=lambda i: 1000.0 + 2 * i):
        return {b.date: f(i) for i, b in enumerate(bars)}

    def test_index_and_excess(self):
        bars = _bars()
        idx = self._idx(bars)
        r = compute_reaction(bars, event_date=date(2026, 10, 7), index_closes=idx)
        ds = [b.date for b in bars]
        i = ds.index(date(2026, 10, 7))
        h = r["returns"]["d5"]
        stock = (bars[i + 4].close / bars[i - 1].close - 1) * 100
        index = (idx[ds[i + 4]] / idx[ds[i - 1]] - 1) * 100
        assert h["index_pct"] == round(index, 2)
        assert h["excess_pct"] == round(stock - index, 2)
        assert h["reason"] is None

    def test_no_index_gives_null_excess_with_reason(self):
        r = compute_reaction(
            _bars(), event_date=date(2026, 10, 7), index_closes=None, index_missing_reason="ETF는 지수 비교 안 함"
        )
        h = r["returns"]["d1"]
        assert h["available"] is True and h["pct"] is not None
        assert h["index_pct"] is None and h["excess_pct"] is None and h["reason"] == "ETF는 지수 비교 안 함"

    def test_index_missing_on_a_date(self):
        bars = _bars()
        idx = self._idx(bars)
        ds = [b.date for b in bars]
        del idx[ds[ds.index(date(2026, 10, 7)) + 4]]
        r = compute_reaction(bars, event_date=date(2026, 10, 7), index_closes=idx)
        assert r["returns"]["d5"]["excess_pct"] is None and "지수 데이터 없음" in r["returns"]["d5"]["reason"]
        assert r["returns"]["d1"]["excess_pct"] is not None

    def test_does_not_modify_inputs(self):
        bars = _bars()
        idx = self._idx(bars)
        bars_copy, idx_copy = list(bars), copy.deepcopy(idx)
        compute_reaction(bars, event_date=date(2026, 10, 7), index_closes=idx)
        assert bars == bars_copy and idx == idx_copy


class TestVolume:
    def test_ratio_uses_20_days_ending_at_base(self):
        bars = _bars(vol=1000.0)
        ds = [b.date for b in bars]
        i = ds.index(date(2026, 10, 7))
        bars[i] = Bar(bars[i].date, bars[i].close, 3000.0)  # 반응 시작일 거래량
        r = compute_reaction(bars, event_date=date(2026, 10, 7))
        assert r["volume_ratio"] == 3.0 and r["volume_sample_days"] == 20

    def test_short_history_uses_what_exists(self):
        bars = _bars(n=45, start=date(2026, 10, 1))  # 기준일 앞에 며칠 없음
        r = compute_reaction(bars, event_date=date(2026, 10, 7))
        base_i = [b.date for b in bars].index(date(2026, 10, 6))
        assert r["volume_sample_days"] == base_i + 1 < 20
        assert r["volume_ratio"] == 1.0

    def test_missing_volume_values_are_skipped(self):
        bars = _bars()
        ds = [b.date for b in bars]
        i = ds.index(date(2026, 10, 6))
        bars[i] = Bar(bars[i].date, bars[i].close, None)
        r = compute_reaction(bars, event_date=date(2026, 10, 7))
        assert r["volume_sample_days"] == 19

    def test_zero_average_is_null(self):
        r = compute_reaction(_bars(vol=0.0), event_date=date(2026, 10, 7))
        assert r["volume_ratio"] is None


class TestAdjustmentWarning:
    def test_big_jump_in_window_warns(self):
        bars = _bars()
        ds = [b.date for b in bars]
        i = ds.index(date(2026, 10, 7))
        bars[i + 2] = Bar(bars[i + 2].date, bars[i + 1].close * 0.4, 1000.0)  # -60%
        r = compute_reaction(bars, event_date=date(2026, 10, 7))
        assert r["note"] and "가격 조정" in r["note"]

    def test_normal_moves_no_note(self):
        assert compute_reaction(_bars(), event_date=date(2026, 10, 7))["note"] is None

    def test_exactly_30_percent_does_not_warn(self):
        bars = [Bar(date(2026, 10, 5), 100.0, 1), Bar(date(2026, 10, 6), 100.0, 1), Bar(date(2026, 10, 7), 130.0, 1)]
        assert compute_reaction(bars, event_date=date(2026, 10, 7))["note"] is None

    def test_jump_after_window_is_ignored(self):
        bars = _bars(n=60)
        ds = [b.date for b in bars]
        i = ds.index(date(2026, 9, 10))
        # 20일 구간(시작일 포함 20거래일) 밖의 급변
        bars[i + 25] = Bar(bars[i + 25].date, bars[i + 24].close * 2, 1000.0)
        r = compute_reaction(bars, event_date=date(2026, 9, 10))
        assert r["note"] is None


class TestOtherEvents:
    def test_window_end_date(self):
        ds = _calendar()
        assert window_end_date(ds, ds[3]) == ds[22]
        assert window_end_date(ds, ds[-5]) == ds[-1]  # 20일이 안 되면 마지막 거래일
        assert window_end_date(ds, None) is None

    def test_count_excludes_self_and_out_of_window(self):
        ds = _calendar()
        starts = [ds[3], ds[3], ds[10], ds[30], None]
        window = (ds[3], window_end_date(ds, ds[3]))
        assert count_other_events(0, starts, window) == 2  # 같은 날 1건 + ds[10]
        assert count_other_events(3, starts, (ds[30], window_end_date(ds, ds[30]))) == 0

    def test_no_window_is_zero(self):
        assert count_other_events(0, [None, None], (None, None)) == 0

    def test_value_is_passed_through(self):
        assert compute_reaction(_bars(), event_date=date(2026, 10, 7), other_events_in_window=4)["other_events_in_window"] == 4


class TestSummarize:
    def _r(self, d1, d5=None, d20=None, e5=None):
        def h(p, e=None):
            return {"pct": p, "index_pct": None, "excess_pct": e, "available": p is not None, "reason": None}

        return {"returns": {"d1": h(d1), "d5": h(d5, e5), "d20": h(d20)}}

    def test_averages_only_over_available(self):
        s = summarize([self._r(1.0, 2.0, None, 1.0), self._r(-3.0, None), self._r(5.0, -4.0, None, -2.0)])
        assert s["event_count"] == 3
        assert s["with_d1"] == 3 and s["avg_d1_pct"] == 1.0 and s["positive_d1_count"] == 2
        assert s["with_d5"] == 2 and s["avg_d5_pct"] == -1.0 and s["positive_d5_count"] == 1
        assert s["with_excess_d5"] == 2 and s["avg_excess_d5_pct"] == -0.5
        assert s["with_d20"] == 0 and s["avg_d20_pct"] is None

    def test_empty(self):
        s = summarize([])
        assert s["event_count"] == 0 and s["avg_d1_pct"] is None and s["with_d5"] == 0
