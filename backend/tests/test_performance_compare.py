"""성과·벤치마크 비교(app/performance_compare, app/performance_i18n) 단위 테스트. DB 없이 합성 체결·종가로 기대값을 손으로 계산해 대조한다.

초기 자금은 손계산이 쉽게 1,000,000원으로 둔다(실제 서비스는 INITIAL_BALANCE). 체결은 체결일 장 마감 종가로 평가한다.
"""

import json
import re
from datetime import date, timedelta

import pytest

from app import performance_compare as pc
from app import performance_i18n as pi
from app.performance import TradeIn, compute_performance
from app.performance_db import replayed_cash
from evals.metrics import forbidden_hits

INIT = 1_000_000.0
LOCALES = ("ko-KR", "en-US", "ja-JP", "zh-CN")


def weekdays(start: date, n: int) -> list[date]:
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


D = weekdays(date(2026, 10, 26), 5)  # 월 10-26 ~ 금 10-30


def buy(day, t, q, p):
    return TradeIn(day, t, t, "buy", q, p)


def sell(day, t, q, p):
    return TradeIn(day, t, t, "sell", q, p)


def series(days, values):
    return [(d, float(v)) for d, v in zip(days, values) if v is not None]


def run(trades, closes, calendar=D, idx=None, period="all", code="KS11", actual_cash="replay", positions="replay", expected=None):
    core = compute_performance(trades, closes, calendar, [], period, INIT, detail=True)
    replayed = replayed_cash(trades, INIT) if trades else None
    if actual_cash == "replay":
        actual_cash = replayed if trades else INIT
    if positions == "replay":
        positions = pc._replay_positions(trades) or {}
    return pc.build(core, trades, replayed, actual_cash, positions, {code: idx if idx is not None else series(calendar, [100] * len(calendar))},
                    code, expected or (calendar[-1] if calendar else D[-1]), closes)


def pt(r, day):
    return next(p for p in r["series"]["points"] if p["date"] == day.isoformat())


# ---------- 보유 수량·현금 복원 ----------


def test_buy_then_daily_valuation():
    r = run([buy(D[1], "A", 10, 100)], {"A": series(D, [100, 100, 110, 90, 120])})
    assert r["status"] == "ok" and r["history_verified"] is True
    # 시작 = 첫 체결 전 거래일(10-26) 초기 자금, 10-27 매수 뒤 현금 999,000 + 10 x 종가
    assert [p["total_asset"] for p in r["series"]["points"]] == [1_000_000, 1_000_000, 1_000_100, 999_900, 1_000_200]
    p = pt(r, D[4])
    assert p["cash_balance"] == 999_000 and p["eval_amount"] == 1_200 and p["cash_balance"] + p["eval_amount"] == p["total_asset"]
    assert r["summary"]["period_return_pct"] == 0.02 and r["summary"]["profit_loss"] == 200


def test_multiple_buys_partial_and_full_sell():
    trades = [buy(D[1], "A", 10, 100), buy(D[2], "A", 5, 100), sell(D[3], "A", 3, 100), sell(D[4], "A", 12, 100)]
    r = run(trades, {"A": series(D, [100] * 5)})
    pts = r["series"]["points"]
    assert [p["eval_amount"] for p in pts] == [0, 1000, 1500, 1200, 0]  # 보유 10 → 15 → 12 → 0
    assert [p["cash_balance"] for p in pts] == [1_000_000, 999_000, 998_500, 998_800, 1_000_000]
    assert all(p["total_asset"] == 1_000_000 for p in pts)  # 매수·매도는 자산 교체라 외부 유입이 아니다
    assert r["summary"]["period_return_pct"] == 0.0


def test_same_day_order_follows_execution_order():
    ok = run([buy(D[1], "A", 10, 100), sell(D[1], "A", 10, 110)], {"A": series(D, [100] * 5)})
    assert pt(ok, D[1])["cash_balance"] == 1_000_100 and pt(ok, D[1])["eval_amount"] == 0
    bad = run([sell(D[1], "A", 10, 110), buy(D[1], "A", 10, 100)], {"A": series(D, [100] * 5)}, actual_cash=1_000_100, positions={})
    assert bad["status"] == "history_unavailable" and bad["series"]["points"] == []  # 보유보다 먼저 판 기록은 재생할 수 없다


def test_no_fees_or_taxes_exist_and_cash_matches_order_engine():
    """이 서비스의 주문은 수수료·세금이 없다(app.portfolio.apply_buy/apply_sell: 체결가 x 수량). 재생도 같은 계산이다."""
    r = run([buy(D[1], "A", 3, 333.33)], {"A": series(D, [333.33] * 5)})
    assert pt(r, D[1])["cash_balance"] == round(1_000_000 - 999.99, 2)
    assert r["cash_flows"] == {"supported": False, "external_flow_count": 0, "return_method": "simple"}


def test_trade_on_weekend_is_valued_on_next_trading_day():
    sat = date(2026, 10, 31)
    cal = weekdays(date(2026, 10, 26), 7)  # ~ 11-03(화)
    r = run([buy(sat, "A", 10, 100)], {"A": series(cal, [100] * 5 + [100, 130])}, calendar=cal)
    assert pt(r, cal[4])["eval_amount"] == 0 and pt(r, cal[5])["eval_amount"] == 1000 and pt(r, cal[6])["total_asset"] == 1_000_300


def test_holdings_and_cash_must_match_the_account():
    trades = [buy(D[1], "A", 10, 100)]
    assert run(trades, {"A": series(D, [100] * 5)}, actual_cash=500_000)["status"] == "history_unavailable"
    r = run(trades, {"A": series(D, [100] * 5)}, positions={"A": 7})
    assert r["status"] == "history_unavailable" and r["summary"]["period_return_pct"] is None and r["comparison"]["status"] == "portfolio_unavailable"
    w = next(x for x in r["warnings"] if x["kind"] == "history_mismatch")
    assert w["parts"] == ["holdings"]


# ---------- 가격 누락 ----------


def test_missing_close_is_previous_close_and_marked_estimated():
    r = run([buy(D[1], "A", 10, 100), buy(D[1], "B", 10, 100)],
            {"A": series(D, [100, 100, 120, 120, 120]), "B": series(D, [100, 100, None, 150, 150])})
    p = pt(r, D[2])
    assert p["estimated"] is True and p["estimated_tickers"] == ["B"] and p["eval_amount"] == 1200 + 1000  # B는 직전 종가 100
    assert r["status"] == "partial" and r["estimated_dates"] == [D[2].isoformat()]
    assert any(w["kind"] == "estimated_prices" and w["count"] == 1 for w in r["warnings"])


def test_no_price_before_listing_uses_trade_price_estimate():
    r = run([buy(D[1], "A", 10, 100)], {"A": series(D, [None, None, None, 105, 106])})
    assert pt(r, D[1])["estimated"] and pt(r, D[2])["eval_amount"] == 1000 and pt(r, D[3])["estimated"] is False


def test_no_prices_and_no_trades_and_no_account():
    assert run([buy(D[1], "A", 1, 1)], {}, calendar=[])["status"] == "no_prices"
    r = pc.build(compute_performance([], {}, D, [], "all", INIT, detail=True), [], None, INIT, {}, {"KS11": []}, "KS11", D[-1], {})
    assert r["status"] == "no_trades" and r["series"]["points"] == [] and r["summary"]["current_cash"] == INIT
    r = pc.build(compute_performance([], {}, D, [], "all", INIT, detail=True), [], None, None, None, {"KS11": []}, "KS11", D[-1], {})
    assert r["status"] == "no_account"
    late = run([buy(D[-1] + timedelta(days=3), "A", 1, 100)], {"A": series(D, [100] * 5)})
    assert late["status"] == "pending_only" and any(w["kind"] == "pending_trades" for w in late["warnings"])


def test_stale_data_warning():
    r = run([buy(D[1], "A", 1, 100)], {"A": series(D, [100] * 5)}, expected=date(2026, 11, 3))
    assert r["data_lag_business_days"] == 2 and r["warnings"][0]["kind"] == "data_delayed"


# ---------- 벤치마크 ----------


def test_benchmark_normalized_and_excess():
    r = run([buy(D[0], "A", 1000, 100)], {"A": series(D, [100, 102, 104, 106, 108])}, idx=series(D, [2000, 2010, 2020, 2030, 2040]))
    c = r["comparison"]
    # 첫 체결이 첫 거래일이라 시작점에 체결이 반영된다. 전체 기간 시작 금액 = 초기 자금 1,000,000
    assert c["status"] == "ok" and c["start_date"] == D[0].isoformat() and c["common_days"] == 5
    assert c["portfolio_return_pct"] == 0.8 and c["benchmark_return_pct"] == 2.0 and c["excess_return_pp"] == -1.2
    assert [x["benchmark_index"] for x in c["points"]] == [100, 100.5, 101, 101.5, 102]
    assert c["benchmark_start_value"] == 2000 and c["benchmark_end_value"] == 2040


def test_benchmark_missing_days_are_excluded_not_filled():
    idx = series(D, [100, None, 110, 120, 130])
    r = run([buy(D[1], "A", 10, 100)], {"A": series(D, [100] * 5)}, idx=idx)
    c = r["comparison"]
    assert c["status"] == "partial" and c["common_days"] == 4 and D[1].isoformat() not in [x["date"] for x in c["points"]]
    assert c["benchmark_return_pct"] == 30.0


def test_benchmark_start_aligned_when_first_day_missing():
    idx = series(D, [None, None, 200, 210, 220])
    r = run([buy(D[1], "A", 10, 100)], {"A": series(D, [100, 100, 110, 120, 130])}, idx=idx)
    c = r["comparison"]
    assert c["start_aligned"] is True and c["start_date"] == D[2].isoformat()
    # 시작을 10-28로 맞춘다: 포트폴리오 1,000,100 → 1,000,300, 지수 200 → 220
    assert c["portfolio_return_pct"] == round((1_000_300 / 1_000_100 - 1) * 100, 2) and c["benchmark_return_pct"] == 10.0


def test_benchmark_no_data_and_insufficient_overlap():
    r = run([buy(D[1], "A", 1, 100)], {"A": series(D, [100] * 5)}, idx=[])
    assert r["comparison"]["status"] == "no_data" and r["status"] == "ok"
    r = run([buy(D[1], "A", 1, 100)], {"A": series(D, [100] * 5)}, idx=series(D, [None, None, None, None, 100]))
    assert r["comparison"]["status"] == "insufficient_overlap" and r["comparison"]["points"] == []


def test_estimated_portfolio_makes_comparison_partial():
    r = run([buy(D[1], "A", 10, 100)], {"A": series(D, [100, 100, None, 100, 100])})
    assert r["status"] == "partial" and r["comparison"]["status"] == "partial"


# ---------- 기간·지표 ----------


LONG = weekdays(date(2025, 1, 6), 480)  # 약 1년 10개월


def test_periods_and_inception_clamp():
    closes = {"A": series(LONG, [100 + i * 0.1 for i in range(len(LONG))])}
    trades = [buy(LONG[300], "A", 1000, 100 + 300 * 0.1)]
    out = {p: run(trades, closes, calendar=LONG, period=p) for p in ("1m", "3m", "6m", "1y", "all")}
    end = LONG[-1]
    assert all(o["end_date"] == end.isoformat() for o in out.values())
    assert out["1m"]["start_date"] > out["3m"]["start_date"] > out["6m"]["start_date"] >= out["1y"]["start_date"]
    # 1y 시작 요청은 첫 체결(LONG[300]) 전이라 첫 체결 직전 거래일부터 계산하고 그 사실을 밝힌다
    assert out["1y"]["requested_start_date"] < out["1y"]["start_date"] == LONG[299].isoformat()
    assert any(w["kind"] == "period_before_inception" for w in out["1y"]["warnings"])
    assert out["all"]["start_date"] == LONG[299].isoformat() and out["all"]["summary"]["start_value"] == INIT
    s1 = out["1m"]["summary"]
    assert s1["period_return_pct"] == round((s1["end_value"] / s1["start_value"] - 1) * 100, 2)


def test_max_drawdown_and_extremes_exact():
    r = run([buy(D[0], "A", 10_000, 100)], {"A": series(D, [100, 110, 88, 99, 120])})
    m = r["metrics"]
    # 자산 = 현금 0 + 10,000 x 종가: 1,000,000 1,100,000 880,000 990,000 1,200,000
    assert m["highest"] == {"value": 1_200_000, "date": D[4].isoformat()} and m["lowest"] == {"value": 880_000, "date": D[2].isoformat()}
    assert m["max_drawdown"]["pct"] == -20.0 and m["max_drawdown"]["peak_date"] == D[1].isoformat()
    assert m["max_drawdown"]["recovery_date"] == D[4].isoformat()


def test_small_sample_metrics_are_null():
    r = run([buy(D[0], "A", 10, 100)], {"A": series(D, [100, 101, 102, 103, 104])})
    m = r["metrics"]
    assert m["daily_return_count"] == 4 and m["daily_volatility_pct"] is None and m["annualized_return_pct"] is None


def test_long_sample_metrics_exist():
    closes = {"A": series(LONG, [100 * (1.001 ** i) for i in range(len(LONG))])}
    r = run([buy(LONG[0], "A", 1000, 100)], closes, calendar=LONG)
    assert r["metrics"]["daily_volatility_pct"] is not None and r["metrics"]["annualized_return_pct"] is not None


def test_price_discontinuity_is_warned():
    r = run([buy(D[1], "A", 10, 100)], {"A": series(D, [100, 100, 50, 50, 50])})
    assert any(w["kind"] == "price_discontinuity" and w["ticker"] == "A" for w in r["warnings"])


# ---------- 언어 ----------


def _sample():
    return run([buy(D[1], "A", 10, 100), buy(D[1], "B", 10, 100)], {"A": series(D, [100, 100, 120, 120, 120]), "B": series(D, [100, 100, None, 150, 150])},
               idx=series(D, [100, None, 110, 120, 130]), expected=date(2026, 11, 3))


def test_numbers_identical_across_locales():
    r = _sample()
    outs = {L: pi.localize(r, L) for L in LOCALES}
    ko = outs["ko-KR"]
    strip = lambda x: {k: v for k, v in x.items() if k not in ("message", "benchmark_name")}  # noqa: E731
    for L, o in outs.items():
        for k in ("summary", "metrics", "series", "estimated_dates", "status", "data_date", "start_date", "end_date", "benchmark_code"):
            assert o[k] == ko[k], (L, k)
        assert strip(o["comparison"]) == strip(ko["comparison"])
        assert [strip(w) for w in o["warnings"]] == [strip(w) for w in ko["warnings"]]
        texts = [w["message"] for w in o["warnings"]] + [o["disclaimer"]] + [x for x in (o["message"], o["comparison"]["message"]) if x]
        assert all(t and "{" not in t for t in texts)
        if L != "ko-KR":
            assert not re.search(r"[가-힣]", " ".join(texts)), L
        assert forbidden_hits(json.dumps(o, ensure_ascii=False)) == []
    assert outs["en-US"]["benchmark_name"] == "KOSPI" and ko["benchmark_name"] == "코스피"


@pytest.mark.parametrize("status", ["no_trades", "history_unavailable"])
def test_status_messages_exist(status):
    for L in LOCALES:
        assert pi.STATUS[status][L] and pi.BENCH["portfolio_unavailable"][L]
