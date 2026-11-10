"""app/briefing.py(순수 계산) 단위 테스트. DB, Ollama, 네트워크 없이 돈다. 기대값은 손으로 계산한 값이다."""

import re
from datetime import date, datetime, timedelta

import pytest

import app.briefing as b
from app.briefing import (
    Bar,
    BriefingInputs,
    DisclosureRow,
    Instrument,
    KST,
    NewsRow,
    build_briefing,
    evaluate_positions,
    make_flags,
    pct_change,
    price_stats,
    quote,
    rank_highlights,
    select_disclosures,
    select_news,
)
from app.disclaimer import DISCLAIMER
from evals.metrics import forbidden_hits

NOW = datetime(2026, 10, 28, 10, 0, tzinfo=KST)  # 수요일
D0 = date(2026, 9, 1)


def bars(closes, volumes=None):
    """closes(오래된 순)로 하루씩 늘어나는 날짜의 Bar 목록. 순서를 일부러 섞어도 price_stats가 날짜로 정렬한다."""
    volumes = volumes or [None] * len(closes)
    return [Bar(D0 + timedelta(days=i), c, v) for i, (c, v) in enumerate(zip(closes, volumes))]


def vol_bars(prior_volume, latest_volume, prior_days=20, close=100.0):
    return bars([close] * (prior_days + 1), [prior_volume] * prior_days + [latest_volume])


class TestPctChange:
    def test_exact_values(self):
        assert pct_change(103, 100) == 3.0
        assert pct_change(97, 100) == -3.0
        assert pct_change(107, 104) == 2.88  # 3/104 = 2.8846%

    @pytest.mark.parametrize("new,old", [(None, 100), (100, None), (100, 0), (100, -5)])
    def test_missing_or_nonpositive_base_is_none(self, new, old):
        assert pct_change(new, old) is None


class TestPriceStats:
    def test_day_and_week_change(self):
        st = price_stats(bars([100, 101, 102, 103, 104, 107]))
        assert st["latest_close"] == 107 and st["latest_date"] == D0 + timedelta(days=5)
        assert st["prev_close"] == 104
        assert st["day_change_pct"] == 2.88
        assert st["week_change_pct"] == 7.0  # 5거래일 전 종가 100 -> 107

    def test_week_needs_six_rows(self):
        st = price_stats(bars([100, 101, 102, 103, 107]))
        assert st["week_change_pct"] is None
        assert st["day_change_pct"] == pytest.approx(3.88, abs=0.005)  # 107/103 - 1

    def test_unordered_input_is_sorted_by_date(self):
        rows = bars([100, 110])
        assert price_stats(rows[::-1])["day_change_pct"] == 10.0

    def test_single_row_has_no_changes(self):
        st = price_stats(bars([100], [500]))
        assert st["data_available"] is True
        assert (st["day_change_pct"], st["week_change_pct"], st["volume_ratio"]) == (None, None, None)
        assert st["volume"] == 500

    def test_no_rows(self):
        st = price_stats([])
        assert st["data_available"] is False and st["latest_close"] is None and st["latest_date"] is None

    def test_rows_with_missing_or_zero_close_are_ignored(self):
        rows = bars([100, None, 0, 110])  # 유효한 행은 100, 110뿐
        st = price_stats(rows)
        assert st["day_change_pct"] == 10.0
        assert st["latest_date"] == D0 + timedelta(days=3)

    def test_only_missing_close_means_no_data(self):
        assert price_stats(bars([None, None]))["data_available"] is False

    def test_volume_ratio(self):
        st = price_stats(vol_bars(100, 230))
        assert st["avg_volume_20d"] == 100.0
        assert st["volume_ratio"] == 2.3
        assert st["volume"] == 230

    def test_volume_average_excludes_latest_day(self):
        # 직전 20일 평균 = (10x10 + 10x30)/20 = 20, 최신 40 -> 2.0배. 최신을 평균에 넣으면 값이 달라진다.
        rows = bars([100.0] * 21, [10] * 10 + [30] * 10 + [40])
        st = price_stats(rows)
        assert st["avg_volume_20d"] == 20.0 and st["volume_ratio"] == 2.0

    def test_volume_ratio_is_none_when_average_is_zero(self):
        st = price_stats(vol_bars(0, 500))
        assert st["volume_ratio"] is None and st["avg_volume_20d"] == 0.0

    def test_volume_ratio_zero_latest_volume(self):
        assert price_stats(vol_bars(100, 0))["volume_ratio"] == 0.0

    def test_volume_ratio_none_with_fewer_than_20_prior_days(self):
        assert price_stats(vol_bars(100, 300, prior_days=19))["volume_ratio"] is None

    def test_volume_ratio_none_when_a_prior_volume_is_missing(self):
        rows = vol_bars(100, 300)
        rows[3] = rows[3]._replace(volume=None)
        assert price_stats(rows)["volume_ratio"] is None

    def test_volume_ratio_none_when_latest_volume_is_missing(self):
        assert price_stats(vol_bars(100, None))["volume_ratio"] is None


class TestSelectNews:
    def row(self, title, hours_ago, url=None, source="연합뉴스"):
        return NewsRow(title, source, url, NOW - timedelta(hours=hours_ago))

    def test_sorted_newest_first_and_limited(self):
        rows = [self.row("a", 30), self.row("b", 2), self.row("c", 10), self.row("d", 50)]
        out = select_news(rows, 3, NOW)
        assert [n["title"] for n in out] == ["b", "c", "a"]

    def test_older_than_7_days_excluded_and_boundary_included(self):
        rows = [self.row("old", 7 * 24 + 1), self.row("edge", 7 * 24), self.row("new", 1)]
        assert [n["title"] for n in select_news(rows, 5, NOW)] == ["new", "edge"]

    def test_rows_without_date_are_excluded(self):
        assert select_news([NewsRow("x", "s", "u", None)], 5, NOW) == []

    def test_duplicate_titles_ignoring_case_spaces_and_punctuation(self):
        rows = [self.row("삼성전자, 호실적 발표!", 5), self.row("삼성전자 호실적 발표", 3), self.row("Samsung Beat", 4),
                self.row("samsung  beat", 8)]
        out = select_news(rows, 5, NOW)
        assert [n["title"] for n in out] == ["삼성전자 호실적 발표", "Samsung Beat"]  # 각각 더 최신 쪽만 남는다

    def test_duplicate_urls(self):
        rows = [self.row("제목 하나", 1, url="https://n.example/1"), self.row("전혀 다른 제목", 2, url="https://n.example/1")]
        assert len(select_news(rows, 5, NOW)) == 1

    def test_duplicates_do_not_use_up_the_limit(self):
        rows = [self.row("같은 기사", 1), self.row("같은 기사", 2), self.row("다른 기사", 3)]
        assert [n["title"] for n in select_news(rows, 2, NOW)] == ["같은 기사", "다른 기사"]

    def test_published_at_is_iso_kst(self):
        out = select_news([self.row("t", 1)], 1, NOW)
        assert out[0]["published_at"] == (NOW - timedelta(hours=1)).isoformat()
        assert set(out[0]) == {"title", "source", "published_at", "url"}


class TestSelectDisclosures:
    def row(self, days_ago, title="공시"):
        return DisclosureRow(title, "정기공시", "https://dart.example/1", NOW - timedelta(days=days_ago))

    def test_count_covers_all_recent_but_items_are_capped(self):
        rows = [self.row(d, f"t{d}") for d in (0, 1, 2, 3, 6, 8, 20)]
        count, items = select_disclosures(rows, NOW)
        assert count == 5  # 8일, 20일 전은 기간 밖
        assert [i["title"] for i in items] == ["t0", "t1", "t2"]

    def test_empty(self):
        assert select_disclosures([], NOW) == (0, [])


def stats_for(**kw):
    base = {"latest_date": date(2026, 10, 27), "day_change_pct": None, "week_change_pct": None, "volume_ratio": None}
    return {**base, **kw}


INST = Instrument("005930", "삼성전자")


def types(flags):
    return [f["type"] for f in flags]


class TestMakeFlags:
    @pytest.mark.parametrize("value,expected", [(3.0, True), (2.99, False), (-3.0, True), (-2.99, False), (0.0, False)])
    def test_day_move_boundary(self, value, expected):
        flags = make_flags(INST, "holding", stats_for(day_change_pct=value), 0, None)
        assert (b.FLAG_DAY_MOVE in types(flags)) is expected

    @pytest.mark.parametrize("value,expected", [(7.0, True), (6.99, False), (-7.0, True), (-6.99, False)])
    def test_week_move_boundary(self, value, expected):
        flags = make_flags(INST, "holding", stats_for(week_change_pct=value), 0, None)
        assert (b.FLAG_WEEK_MOVE in types(flags)) is expected

    @pytest.mark.parametrize("value,expected", [(2.0, True), (1.99, False), (5.0, True), (0.0, False)])
    def test_volume_spike_boundary(self, value, expected):
        flags = make_flags(INST, "holding", stats_for(volume_ratio=value), 0, None)
        assert (b.FLAG_VOLUME_SPIKE in types(flags)) is expected

    @pytest.mark.parametrize("value,expected", [(40.0, True), (39.99, False), (100.0, True)])
    def test_concentration_boundary(self, value, expected):
        flags = make_flags(INST, "holding", stats_for(), 0, value)
        assert (b.FLAG_CONCENTRATION in types(flags)) is expected

    @pytest.mark.parametrize("count,expected", [(1, True), (0, False)])
    def test_new_disclosure_boundary(self, count, expected):
        assert (b.FLAG_NEW_DISCLOSURE in types(make_flags(INST, "watchlist", stats_for(), count, None))) is expected

    def test_none_values_never_flag(self):
        assert make_flags(INST, "holding", stats_for(), 0, None) == []

    def test_flag_content(self):
        (f,) = make_flags(INST, "holding", stats_for(day_change_pct=-3.5), 0, None)
        assert f == {
            "type": "day_move", "scope": "holding", "ticker": "005930", "name": "삼성전자",
            "text": "삼성전자의 2026-10-27 종가는 전 거래일 대비 -3.50% 하락했습니다.",
            "value": -3.5, "threshold": 3.0, "unit": "%", "date": "2026-10-27", "direction": "down",
        }

    def test_volume_text_states_ratio_and_window(self):
        (f,) = make_flags(INST, "holding", stats_for(volume_ratio=2.3), 0, None)
        assert f["text"] == "삼성전자의 거래량이 직전 20거래일 평균의 2.30배입니다."
        assert f["direction"] is None and f["unit"] == "배"

    def test_order_follows_flag_types(self):
        flags = make_flags(INST, "holding", stats_for(day_change_pct=4, week_change_pct=8, volume_ratio=3), 2, 50.0)
        assert types(flags) == list(b.FLAG_TYPES)

    def test_thresholds_are_the_documented_values(self):
        assert (b.DAY_MOVE_PCT, b.WEEK_MOVE_PCT, b.VOLUME_SPIKE_RATIO, b.CONCENTRATION_PCT) == (3.0, 7.0, 2.0, 40.0)
        assert (b.RECENT_DAYS, b.MAX_HIGHLIGHTS) == (7, 5)

    def test_concentration_threshold_matches_diagnosis(self):
        from app.diagnosis import CONCENTRATION_TOP1_PCT

        assert b.CONCENTRATION_PCT == CONCENTRATION_TOP1_PCT


class TestRankHighlights:
    def flag(self, kind, scope, ticker, value, threshold):
        return {"type": kind, "scope": scope, "ticker": ticker, "value": value, "threshold": threshold}

    def test_holding_before_watchlist_then_type_then_magnitude_then_ticker(self):
        flags = [
            self.flag("day_move", "watchlist", "W1", 20.0, 3.0),
            self.flag("new_disclosure", "holding", "H1", 1.0, 1.0),
            self.flag("volume_spike", "holding", "H2", 9.0, 2.0),
            self.flag("day_move", "holding", "H3", -4.0, 3.0),
            self.flag("day_move", "holding", "H4", 9.0, 3.0),
            self.flag("day_move", "holding", "H0", 9.0, 3.0),
            self.flag("concentration", "holding", "H5", 45.0, 40.0),
        ]
        out = [(f["scope"], f["ticker"]) for f in rank_highlights(flags)]
        assert out == [("holding", "H5"), ("holding", "H0"), ("holding", "H4"), ("holding", "H3"), ("holding", "H2")]

    def test_negative_value_ranks_by_absolute_size(self):
        flags = [self.flag("day_move", "holding", "A", 3.5, 3.0), self.flag("day_move", "holding", "B", -8.0, 3.0)]
        assert [f["ticker"] for f in rank_highlights(flags)] == ["B", "A"]

    def test_limit(self):
        flags = [self.flag("day_move", "holding", f"T{i}", 4.0, 3.0) for i in range(9)]
        assert len(rank_highlights(flags)) == 5
        assert len(rank_highlights(flags, limit=2)) == 2


class TestEvaluatePositions:
    def test_hand_computed_values(self):
        a = Instrument("A", "에이", "stock", 10, 65_000.0)
        c = Instrument("B", "비", "stock", 5, 22_000.0)
        sa = price_stats(bars([68_000.0, 70_000.0]))
        sb = price_stats(bars([21_000.0, 20_000.0]))
        ev = evaluate_positions(1_000_000.0, [(a, sa), (c, sb)])
        # 평가 A 700,000 / B 100,000 -> 평가합 800,000, 총자산 1,800,000
        assert ev["holdings_value"] == 800_000.0 and ev["total_asset"] == 1_800_000.0
        assert ev["by_ticker"]["A"] == {
            "eval_amount": 700_000.0, "profit_loss": 50_000.0, "profit_loss_pct": 7.69,  # 70000/65000 = 1.0769
            "day_profit_loss": 20_000.0, "weight_pct": 38.89,  # 700/1800
        }
        assert ev["by_ticker"]["B"] == {
            "eval_amount": 100_000.0, "profit_loss": -10_000.0, "profit_loss_pct": -9.09,  # 20000/22000 = 0.9091
            "day_profit_loss": -5_000.0, "weight_pct": 5.56,  # 100/1800
        }
        assert ev["day_profit_loss"] == 15_000.0
        assert ev["unpriced"] == []

    def test_unpriced_holding_is_left_out_of_totals(self):
        a = Instrument("A", "에이", "stock", 10, 65_000.0)
        n = Instrument("N", "무시세", "stock", 3, 10_000.0)
        ev = evaluate_positions(300_000.0, [(a, price_stats(bars([68_000.0, 70_000.0]))), (n, price_stats([]))])
        assert ev["holdings_value"] == 700_000.0 and ev["total_asset"] == 1_000_000.0
        assert ev["by_ticker"]["N"] == {
            "eval_amount": None, "profit_loss": None, "profit_loss_pct": None, "day_profit_loss": None,
            "weight_pct": None,
        }
        assert ev["by_ticker"]["A"]["weight_pct"] == 70.0
        assert ev["unpriced"] == ["무시세"]

    def test_single_day_of_prices_has_no_day_profit_loss(self):
        a = Instrument("A", "에이", "stock", 10, 65_000.0)
        ev = evaluate_positions(0.0, [(a, price_stats(bars([70_000.0])))])
        assert ev["by_ticker"]["A"]["day_profit_loss"] is None and ev["day_profit_loss"] is None
        assert ev["by_ticker"]["A"]["weight_pct"] == 100.0

    def test_zero_total_asset_gives_no_weight(self):
        a = Instrument("A", "에이", "stock", 1, 1.0)
        ev = evaluate_positions(0.0, [(a, price_stats([]))])
        assert ev["total_asset"] == 0.0 and ev["by_ticker"]["A"]["weight_pct"] is None


class TestQuote:
    def test_latest_and_change(self):
        q = quote("KS11", "코스피", [(date(2026, 10, 26), 2500.0), (date(2026, 10, 27), 2550.0)])
        assert q == {"code": "KS11", "name": "코스피", "date": "2026-10-27", "close": 2550.0, "change_pct": 2.0}

    def test_single_row_has_no_change_and_empty_is_none(self):
        assert quote("X", "엑스", [(date(2026, 10, 27), 10.0)])["change_pct"] is None
        assert quote("X", "엑스", []) is None


def full_bars(close_prev, close, volume=1000, n=21):
    """n일치 시세. 마지막 두 날의 종가가 close_prev, close이고 나머지는 같은 값, 거래량은 일정."""
    closes = [close_prev] * (n - 1) + [close]
    return bars(closes, [volume] * n)


class TestBuildBriefing:
    def inputs(self, **kw):
        base = dict(
            has_account=True,
            cash_balance=5_000_000.0,
            holdings=[Instrument("005930", "삼성전자", "stock", 10, 60_000.0)],
            watchlist=[Instrument("000660", "SK하이닉스", "stock")],
            bars={
                "005930": full_bars(70_000.0, 74_200.0),  # +6.00%
                "000660": full_bars(100_000.0, 101_000.0),
            },
        )
        base.update(kw)
        return BriefingInputs(**base)

    def test_empty_user_gets_message_and_market(self):
        out = build_briefing(
            BriefingInputs(kospi=[(date(2026, 10, 27), 2500.0), (date(2026, 10, 26), 2400.0)]), 3, NOW
        )
        assert out["message"] == "관심종목을 추가하면 개인화된 브리핑을 볼 수 있습니다."
        assert out["holdings"] == [] and out["watchlist"] == [] and out["highlights"] == []
        assert out["market"]["kospi"]["change_pct"] == 4.17 and out["market"]["kosdaq"] is None
        assert out["data_date"] == "2026-10-27" and out["stale"] is False
        assert out["portfolio"]["has_account"] is False and out["portfolio"]["cash_balance"] is None
        assert out["portfolio"]["message"] and out["disclaimer"] == DISCLAIMER

    def test_no_data_at_all(self):
        out = build_briefing(BriefingInputs(), 3, NOW)
        assert out["data_date"] is None and out["stale"] is True
        assert out["stale_notice"] == "수집된 시세 데이터가 없습니다."
        assert out["market"] == {"kospi": None, "kosdaq": None, "usd_krw": None}

    def test_account_without_holdings(self):
        out = build_briefing(BriefingInputs(has_account=True, cash_balance=10_000_000.0), 3, NOW)
        p = out["portfolio"]
        assert p["has_account"] is True and p["total_asset"] == 10_000_000.0 and p["profit_loss"] == 0.0
        assert p["profit_loss_pct"] == 0.0 and p["top_weight"] is None and p["weights"] == []
        assert "보유 중인 종목이 없습니다" in p["message"]

    def test_portfolio_numbers(self):
        out = build_briefing(self.inputs(), 3, NOW)
        p = out["portfolio"]
        # 평가 742,000, 총자산 5,742,000, 초기 10,000,000 대비 -4,258,000 (-42.58%)
        assert p["holdings_value"] == 742_000.0 and p["total_asset"] == 5_742_000.0
        assert p["profit_loss"] == -4_258_000.0 and p["profit_loss_pct"] == -42.58
        assert p["day_profit_loss"] == 42_000.0  # 10 x (74,200 - 70,000)
        assert p["top_weight"] == {"ticker": "005930", "name": "삼성전자", "weight_pct": 12.92}  # 742/5742
        h = out["holdings"][0]
        assert (h["eval_amount"], h["profit_loss"], h["profit_loss_pct"]) == (742_000.0, 142_000.0, 23.67)
        assert h["day_change_pct"] == 6.0 and h["day_profit_loss"] == 42_000.0

    def test_holding_in_watchlist_is_listed_only_once(self):
        both = self.inputs(watchlist=[Instrument("005930", "삼성전자"), Instrument("000660", "SK하이닉스")])
        out = build_briefing(both, 3, NOW)
        assert [i["ticker"] for i in out["holdings"]] == ["005930"]
        assert [i["ticker"] for i in out["watchlist"]] == ["000660"]

    def test_item_without_data_does_not_affect_others(self):
        inp = self.inputs(watchlist=[Instrument("999999", "무시세"), Instrument("000660", "SK하이닉스")])
        out = build_briefing(inp, 3, NOW)
        empty, ok = out["watchlist"]
        assert empty["data_available"] is False and empty["latest_close"] is None and empty["flags"] == []
        assert empty["news"] == [] and empty["disclosures"] == [] and empty["day_change_pct"] is None
        assert ok["data_available"] is True and ok["day_change_pct"] == 1.0

    def test_unpriced_holding_is_listed_with_message(self):
        inp = self.inputs(holdings=[Instrument("999999", "무시세", "stock", 3, 1000.0)], bars={})
        out = build_briefing(inp, 3, NOW)
        assert out["holdings"][0]["data_available"] is False and out["holdings"][0]["weight_pct"] is None
        assert "무시세" in out["portfolio"]["message"]
        assert out["portfolio"]["total_asset"] == 5_000_000.0

    def test_holdings_sorted_by_weight(self):
        inp = self.inputs(
            holdings=[
                Instrument("S", "작은", "stock", 1, 1000.0),
                Instrument("L", "큰", "stock", 10, 1000.0),
                Instrument("N", "무시세", "stock", 1, 1000.0),
            ],
            watchlist=[],
            bars={"S": full_bars(1000.0, 1000.0), "L": full_bars(1000.0, 1000.0)},
        )
        assert [i["ticker"] for i in build_briefing(inp, 3, NOW)["holdings"]] == ["L", "S", "N"]

    def test_concentration_flag_and_highlight_order(self):
        # 삼성전자 평가 742,000 / 총자산 1,000,000 -> 74.2%
        inp = self.inputs(cash_balance=258_000.0)
        out = build_briefing(inp, 3, NOW)
        flags = out["holdings"][0]["flags"]
        assert types(flags) == ["concentration", "day_move"]
        assert out["highlights"][0]["type"] == "concentration" and out["highlights"][0]["value"] == 74.2

    def test_stale_notice(self):
        old = {k: [Bar(D0, r.close, r.volume) for r in v[:1]] for k, v in self.inputs().bars.items()}
        out = build_briefing(self.inputs(bars=old), 3, NOW)
        assert out["data_date"] == "2026-09-01" and out["stale"] is True
        assert out["stale_notice"] == "마지막 데이터는 2026-09-01 기준입니다."

    @pytest.mark.parametrize("last,stale", [(date(2026, 10, 28), False), (date(2026, 10, 27), False),
                                            (date(2026, 10, 26), True)])
    def test_stale_threshold_is_two_business_days(self, last, stale):
        bars_ = {"005930": [Bar(last, 100.0, 1)]}
        after_close = datetime(2026, 10, 28, 16, 0, tzinfo=KST)  # 마감 뒤라 오늘(10-28) 행도 종가로 쓴다
        out = build_briefing(self.inputs(bars=bars_, watchlist=[]), 3, after_close)
        assert out["stale"] is stale and (out["stale_notice"] is not None) is stale

    def test_news_and_disclosure_flow_into_items(self):
        inp = self.inputs(
            news={"005930": [NewsRow("기사", "연합", "https://n/1", NOW - timedelta(hours=1))]},
            disclosures={"005930": [DisclosureRow("공시", "정기", "https://d/1", NOW - timedelta(days=1))]},
        )
        h = build_briefing(inp, 3, NOW)["holdings"][0]
        assert [n["title"] for n in h["news"]] == ["기사"] and h["disclosure_count"] == 1
        assert "new_disclosure" in types(h["flags"])

    def test_leveraged_etf_gets_short_notice_and_plain_etf_does_not(self):
        inp = BriefingInputs(
            watchlist=[Instrument("122630", "KODEX 레버리지", "etf"), Instrument("069500", "KODEX 200", "etf"),
                       Instrument("005930", "삼성전자레버리지", "stock")],
            bars={},
        )
        out = build_briefing(inp, 3, NOW)
        by = {i["ticker"]: i for i in out["watchlist"]}
        assert "레버리지 ETF입니다" in by["122630"]["notice"]
        assert by["069500"]["notice"] is None
        assert by["005930"]["notice"] is None  # ETF가 아니면 이름만으로 붙이지 않는다

    def test_item_key_sets(self):
        out = build_briefing(self.inputs(), 3, NOW)
        common = {"ticker", "name", "asset_type", "data_available", "latest_close", "latest_date", "day_change_pct",
                  "week_change_pct", "volume", "avg_volume_20d", "volume_ratio", "news", "disclosures",
                  "disclosure_count", "notice", "flags"}
        assert set(out["watchlist"][0]) == common
        assert set(out["holdings"][0]) == common | {"quantity", "avg_price", "eval_amount", "weight_pct", "profit_loss",
                                                    "profit_loss_pct", "day_profit_loss"}


class TestOpenSession:
    """장 마감(평일 15:30 KST) 전에는 오늘 날짜의 행(장중 값)을 쓰지 않는다."""

    def test_session_is_open(self):
        at = lambda *a: datetime(*a, tzinfo=KST)
        assert b.session_is_open(at(2026, 10, 28, 9, 0)) is True  # 수요일 장중
        assert b.session_is_open(at(2026, 10, 28, 15, 29)) is True
        assert b.session_is_open(at(2026, 10, 28, 15, 30)) is False  # 15:30 정각부터는 종가
        assert b.session_is_open(at(2026, 10, 28, 20, 0)) is False
        assert b.session_is_open(at(2026, 10, 31, 10, 0)) is False  # 토요일

    def test_todays_row_is_dropped_before_close_and_kept_after(self):
        prev, today = date(2026, 10, 27), date(2026, 10, 28)
        rows = [Bar(prev, 100.0, 10), Bar(today, 120.0, 3)]
        inp = BriefingInputs(watchlist=[Instrument("A", "에이")], bars={"A": rows},
                             kospi=[(prev, 2500.0), (today, 2600.0)])
        before = build_briefing(inp, 3, datetime(2026, 10, 28, 10, 0, tzinfo=KST))
        a = before["watchlist"][0]
        assert (a["latest_date"], a["latest_close"], a["day_change_pct"]) == ("2026-10-27", 100.0, None)
        assert before["data_date"] == "2026-10-27" and before["market"]["kospi"]["date"] == "2026-10-27"
        after = build_briefing(inp, 3, datetime(2026, 10, 28, 16, 0, tzinfo=KST))
        a = after["watchlist"][0]
        assert (a["latest_date"], a["latest_close"], a["day_change_pct"]) == ("2026-10-28", 120.0, 20.0)
        assert after["market"]["kospi"]["change_pct"] == 4.0

    def test_partial_day_volume_does_not_distort_the_ratio(self):
        # 10-07 ~ 10-27 21거래일 거래량 100, 10-28(오늘) 반나절 거래량 5
        rows = [Bar(date(2026, 10, 7) + timedelta(days=i), 100.0, 100) for i in range(21)]
        rows.append(Bar(date(2026, 10, 28), 100.0, 5))
        inp = BriefingInputs(watchlist=[Instrument("A", "에이")], bars={"A": rows})
        during = build_briefing(inp, 3, datetime(2026, 10, 28, 10, 0, tzinfo=KST))["watchlist"][0]
        assert (during["latest_date"], during["volume_ratio"]) == ("2026-10-27", 1.0)  # 직전 거래일 기준(100 / 100)
        closed = build_briefing(inp, 3, datetime(2026, 10, 28, 16, 0, tzinfo=KST))["watchlist"][0]
        assert (closed["latest_date"], closed["volume_ratio"]) == ("2026-10-28", 0.05)  # 5 / 100

    def test_disclosure_flag_date_is_the_latest_disclosure_date(self):
        inp = BriefingInputs(
            watchlist=[Instrument("A", "에이")], bars={"A": full_bars(100.0, 100.0)},
            disclosures={"A": [DisclosureRow("공시", "t", "u", NOW - timedelta(days=2))]},
        )
        (f,) = build_briefing(inp, 3, NOW)["watchlist"][0]["flags"]
        assert f["type"] == "new_disclosure" and f["date"] == (NOW - timedelta(days=2)).date().isoformat()


# 투자 권유로 읽힐 수 있는 낱말은 아예 쓰지 않는다(evals의 금지 표현 정규식보다 넓은 검사).
ADVICE_WORDS = re.compile(r"매수|매도|사세요|파세요|팔아|추천|권유|권장|유망|담으세요|정리하세요|비중\s*(?:을\s*)?(?:줄|늘)")


class TestNoInvestmentAdvice:
    def all_texts(self):
        # 모든 flag 유형을 양방향으로 켠 항목 + 안내 문구
        texts = []
        for sign in (1, -1):
            st = stats_for(day_change_pct=5.0 * sign, week_change_pct=9.0 * sign, volume_ratio=3.0)
            flags = make_flags(Instrument("T", "테스트", "stock"), "holding", st, 4, 55.0)
            assert set(types(flags)) == set(b.FLAG_TYPES)
            texts += [f["text"] for f in flags]
        texts += [b.EMPTY_MESSAGE, b.NO_HOLDINGS_MESSAGE, b.NO_ACCOUNT_MESSAGE, b.LEVERAGE_NOTICE.format(kind="인버스 레버리지")]
        stale = build_briefing(BriefingInputs(), 3, NOW)
        texts.append(stale["stale_notice"])
        return texts

    def test_flag_texts_pass_the_eval_forbidden_expression_check(self):
        for text in self.all_texts():
            assert forbidden_hits(text) == [], text

    def test_no_advice_vocabulary_anywhere(self):
        for text in self.all_texts():
            assert not ADVICE_WORDS.search(text), text

    def test_full_response_strings_pass_the_check(self):
        inp = BriefingInputs(
            has_account=True, cash_balance=1_000.0,
            holdings=[Instrument("A", "에이", "stock", 100, 10.0)], watchlist=[Instrument("122630", "KODEX 인버스2X", "etf")],
            bars={"A": full_bars(10.0, 13.0, volume=100), "122630": full_bars(10.0, 9.0)},
            news={"A": [NewsRow("기사 제목", "s", "u", NOW - timedelta(hours=1))]},
            disclosures={"A": [DisclosureRow("공시", "t", "u", NOW - timedelta(hours=2))]},
        )
        out = build_briefing(inp, 3, NOW)

        def strings(x):
            if isinstance(x, str):
                yield x
            elif isinstance(x, dict):
                for k, v in x.items():
                    if k not in ("news", "disclosures", "disclaimer"):  # 기사·공시 제목과 고정 면책문구는 원문 그대로
                        yield from strings(v)
            elif isinstance(x, list):
                for v in x:
                    yield from strings(v)

        for s in strings(out):
            assert forbidden_hits(s) == [] and not ADVICE_WORDS.search(s), s
