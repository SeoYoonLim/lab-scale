"""백테스트 엔진(app/backtest.py)과 POST /api/backtest(mock). DB 없음. 실제 DB 대조는 test_backtest_devdb.py.

손계산 예제는 짧은 가격표라 min_days를 낮춰 부른다(API는 기본 30일).
"""

import random
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

import app.api.backtest as backtest_api
import app.backtest_db as backtest_db
from app import rate_limit as RL
from app.backtest import (
    ASSUMPTIONS,
    PAST_PERFORMANCE,
    BacktestError,
    ParamError,
    run_backtest,
    signals,
    validate_params,
)
from app.disclaimer import DISCLAIMER
from app.main import app
from evals.metrics import forbidden_hits

client = TestClient(app, raise_server_exceptions=False)


def days_from(start: date, n: int) -> list[date]:
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def run(prices, strategy, params=None, cash=10_000, fee=0.0, tax=0.0, index=None, min_days=2, **kw):
    first = min(rows[0][0] for rows in prices.values())
    last = max(rows[-1][0] for rows in prices.values())
    return run_backtest(prices, {}, strategy, params, kw.pop("start", first), kw.pop("end", last), cash, fee, tax,
                        index_series=index, min_days=min_days, **kw)


def values(out):
    return [p["total_value"] for p in out["curve"]["points"]]


def trade_rows(out):
    return [(t["side"], t["signal_date"], t["date"], t["quantity"], t["price"], t["cash_after"]) for t in out["trades"]["items"]]


D = days_from(date(2026, 1, 5), 9)  # 2026-01-05(월) ~ 01-15(목)


# ---------- 손계산 예제 ----------


class TestBuyAndHoldByHand:
    """A 100/110/90/120/105, 현금 10,000, 수수료 0.1%, 매도세 0.2%(매도 없음).
    D0 신호 → D1 110원 체결: floor(10,000 ÷ (110 × 1.001)) = floor(90.82) = 90주, 9,900 + 수수료 9.9 → 현금 90.1.
    자산 10,000 · 9,990.1 · 8,190.1 · 10,890.1 · 9,540.1 → 총수익률 −4.60%.
    MDD: D0 10,000 → D2 8,190.1 = −18.10%, D3에 회복."""

    @pytest.fixture
    def out(self):
        return run({"A": list(zip(D[:5], [100, 110, 90, 120, 105]))}, "buy_and_hold", fee=0.001, tax=0.002)

    def test_trade(self, out):
        assert trade_rows(out) == [("buy", "2026-01-05", "2026-01-06", 90, 110, 90.1)]
        assert out["trades"]["items"][0]["fee"] == 9.9 and out["trades"]["items"][0]["amount"] == 9900

    def test_curve_and_metrics(self, out):
        assert values(out) == [10_000, 9_990.1, 8_190.1, 10_890.1, 9_540.1]
        m = out["metrics"]
        assert m["final_value"] == 9_540.1 and m["total_return_pct"] == -4.6
        assert m["ending_cash"] == 90.1 and m["fees_paid"] == 9.9 and m["taxes_paid"] == 0
        assert m["max_drawdown"] == {"pct": -18.1, "peak_date": "2026-01-05", "trough_date": "2026-01-07",
                                     "recovery_date": "2026-01-08", "duration_days": 2}
        assert m["trade_count"] == 1 and m["buy_count"] == 1 and m["sell_count"] == 0


class TestSmaCrossByHand:
    """short 2, long 3, 비용 0, 현금 1,000. 종가 10/10/10/12/14/11/8/9/12.
    i2: SMA2 10, SMA3 10 → 10 > 10 아님(현금 유지).  i3: SMA2 11 > SMA3 10.67 → 매수 신호 → i4 14원: 71주, 현금 6.
    i6: SMA2 9.5 < SMA3 11 → 매도 신호 → i7 9원: 639원 → 현금 645.  i8: 10.5 > 9.67 매수 신호지만 마지막 날이라 체결 없음.
    자산 1,000 ×5 · 787 · 574 · 645 · 645 → −35.5%. MDD 1,000 → 574 = −42.6%."""

    CLOSES = [10, 10, 10, 12, 14, 11, 8, 9, 12]

    @pytest.fixture
    def out(self):
        return run({"A": list(zip(D, self.CLOSES))}, "sma_cross", {"short": 2, "long": 3}, cash=1_000)

    def test_signals(self):
        assert signals("sma_cross", {"short": 2, "long": 3}, D, self.CLOSES) == [
            None, None, "sell", "buy", "buy", "buy", "sell", "sell", "buy"]

    def test_trades(self, out):
        assert trade_rows(out) == [
            ("buy", "2026-01-08", "2026-01-09", 71, 14, 6),
            ("sell", "2026-01-13", "2026-01-14", 71, 9, 645),
        ]

    def test_curve_and_metrics(self, out):
        assert values(out) == [1_000, 1_000, 1_000, 1_000, 1_000, 787, 574, 645, 645]
        m = out["metrics"]
        assert (m["final_value"], m["total_return_pct"], m["max_drawdown"]["pct"]) == (645, -35.5, -42.6)
        assert m["max_drawdown"]["trough_date"] == "2026-01-13" and m["max_drawdown"]["recovery_date"] is None
        assert m["trade_count"] == 2


class TestDcaByHand:
    """매월 30,000원, 현금 100,000, 비용 0. 1/5 1,000, 1/6 2,000, 2/2 2,500, 2/3 5,000, 3/2 4,000, 3/3 3,000.
    신호: 각 달 첫 거래일(1/5, 2/2, 3/2) → 다음 거래일 체결. 1/6 2,000원 15주(현금 70,000), 2/3 5,000원 6주(40,000),
    3/3 3,000원 10주(10,000). 자산 100,000 · 100,000 · 107,500 · 145,000 · 124,000 · 103,000 → +3.00%.
    MDD 145,000 → 103,000 = −28.97%."""

    DAYS = [date(2026, 1, 5), date(2026, 1, 6), date(2026, 2, 2), date(2026, 2, 3), date(2026, 3, 2), date(2026, 3, 3)]
    CLOSES = [1_000, 2_000, 2_500, 5_000, 4_000, 3_000]

    def test_by_hand(self):
        out = run({"A": list(zip(self.DAYS, self.CLOSES))}, "dca", {"monthly_amount": 30_000}, cash=100_000)
        assert trade_rows(out) == [
            ("buy", "2026-01-05", "2026-01-06", 15, 2_000, 70_000),
            ("buy", "2026-02-02", "2026-02-03", 6, 5_000, 40_000),
            ("buy", "2026-03-02", "2026-03-03", 10, 3_000, 10_000),
        ]
        assert values(out) == [100_000, 100_000, 107_500, 145_000, 124_000, 103_000]
        m = out["metrics"]
        assert (m["total_return_pct"], m["max_drawdown"]["pct"], m["ending_cash"]) == (3.0, -28.97, 10_000)
        assert out["per_ticker"][0]["quantity"] == 31

    def test_runs_out_of_cash(self):
        out = run({"A": list(zip(self.DAYS, self.CLOSES))}, "dca", {"monthly_amount": 60_000}, cash=100_000)
        assert [t[3] for t in trade_rows(out)] == [30, 8]  # 60,000/2,000, 남은 40,000/5,000, 세 번째는 현금 0
        assert any("건너뛴 경우가 1번" in n for n in out["notices"])


# ---------- 룩어헤드 방지 ----------


def _random_closes(n, seed):
    rnd = random.Random(seed)
    v, out = 100.0, []
    for _ in range(n):
        v *= 1 + rnd.uniform(-0.04, 0.04)
        out.append(round(v, 2))
    return out


class TestNoLookahead:
    DAYS = days_from(date(2025, 1, 1), 120)

    @pytest.mark.parametrize("strategy, params", [("sma_cross", {"short": 5, "long": 20}), ("dca", {"monthly_amount": 100_000}),
                                                  ("buy_and_hold", {})])
    @pytest.mark.parametrize("k", [25, 60, 100])
    def test_changing_the_future_does_not_change_the_past(self, strategy, params, k):
        base = _random_closes(len(self.DAYS), 1)
        future = base[: k + 1] + [x * 3 if i % 2 else x / 3 for i, x in enumerate(base[k + 1:])]
        a = run({"A": list(zip(self.DAYS, base))}, strategy, params, cash=1_000_000)
        b = run({"A": list(zip(self.DAYS, future))}, strategy, params, cash=1_000_000)
        cut = self.DAYS[k].isoformat()
        # k일까지의 신호는 같다
        assert signals(strategy, validate_params(strategy, params, 1_000_000), self.DAYS, base)[: k + 1] == \
            signals(strategy, validate_params(strategy, params, 1_000_000), self.DAYS, future)[: k + 1]
        # k일까지 체결된 거래와 k일까지의 자산 곡선은 완전히 같다
        assert [t for t in a["trades"]["items"] if t["date"] <= cut] == [t for t in b["trades"]["items"] if t["date"] <= cut]
        assert [p for p in a["curve"]["points"] if p["date"] <= cut] == [p for p in b["curve"]["points"] if p["date"] <= cut]
        # k일 신호로 k+1일에 체결된 거래는 날짜·방향이 같다(가격만 k+1일 종가로 달라진다)
        nxt = self.DAYS[k + 1].isoformat()
        assert [(t["side"], t["signal_date"]) for t in a["trades"]["items"] if t["date"] == nxt] == \
            [(t["side"], t["signal_date"]) for t in b["trades"]["items"] if t["date"] == nxt]

    def test_signal_is_a_function_of_the_prefix_only(self):
        closes = _random_closes(len(self.DAYS), 7)
        full = signals("sma_cross", {"short": 3, "long": 10}, self.DAYS, closes)
        for i in range(len(self.DAYS)):
            assert signals("sma_cross", {"short": 3, "long": 10}, self.DAYS[: i + 1], closes[: i + 1])[-1] == full[i]

    def test_execution_is_always_the_next_trading_day_close(self):
        closes = _random_closes(len(self.DAYS), 3)
        out = run({"A": list(zip(self.DAYS, closes))}, "sma_cross", {"short": 3, "long": 8}, cash=1_000_000)
        assert out["trades"]["total"] >= 4
        idx = {d.isoformat(): i for i, d in enumerate(self.DAYS)}
        for t in out["trades"]["items"]:
            assert idx[t["date"]] == idx[t["signal_date"]] + 1
            assert t["price"] == closes[idx[t["date"]]]

    def test_a_jump_tomorrow_is_not_bought_today(self):
        """내일 50% 오르는 가격표: 신호가 오늘 생겨도 체결은 내일(오른 뒤) 종가라 그 상승을 공짜로 얻지 못한다."""
        out = run({"A": list(zip(D[:4], [100, 100, 150, 150]))}, "buy_and_hold", cash=1_000)
        assert trade_rows(out)[0][4] == 100  # D1 종가 100으로 체결(D0 신호)
        out = run({"A": list(zip(D[:4], [100, 150, 150, 150]))}, "buy_and_hold", cash=1_000)
        assert trade_rows(out)[0][4] == 150 and out["metrics"]["total_return_pct"] == 0.0


# ---------- 비용 ----------


class TestCosts:
    def test_sell_fee_and_tax(self):
        """종가 100/100/100/200/200/100/100, short 2·long 3, 현금 1,020, 수수료 1%, 매도세 0.5%.
        i3 매수 신호 → i4 200원: floor(1,020 ÷ 202) = 5주, 1,000 + 수수료 10 → 현금 10.
        i5 매도 신호(SMA2 150 < SMA3 166.7) → i6 100원: 500 − 수수료 5 − 세금 2.5 → 현금 502.5."""
        closes = [100, 100, 100, 200, 200, 100, 100]
        out = run({"A": list(zip(D[:7], closes))}, "sma_cross", {"short": 2, "long": 3}, cash=1_020, fee=0.01, tax=0.005)
        buy, sell = out["trades"]["items"]
        assert (buy["quantity"], buy["price"], buy["fee"], buy["cash_after"]) == (5, 200, 10.0, 10.0)
        assert (sell["quantity"], sell["price"], sell["fee"], sell["tax"], sell["cash_after"]) == (5, 100, 5.0, 2.5, 502.5)
        assert out["metrics"]["final_value"] == 502.5
        m = out["metrics"]
        assert m["fees_paid"] == pytest.approx(sum(t["fee"] for t in out["trades"]["items"]))
        assert m["taxes_paid"] == pytest.approx(sum(t["tax"] for t in out["trades"]["items"]))
        assert sell["tax"] == pytest.approx(sell["amount"] * 0.005)
        assert sell["cash_after"] == pytest.approx(buy["cash_after"] + sell["amount"] * (1 - 0.01 - 0.005))

    def test_costs_lower_the_result(self):
        prices = {"A": list(zip(TestNoLookahead.DAYS, _random_closes(120, 5)))}
        free = run(prices, "sma_cross", {"short": 3, "long": 8}, cash=1_000_000)
        paid = run(prices, "sma_cross", {"short": 3, "long": 8}, cash=1_000_000, fee=0.001, tax=0.002)
        assert paid["metrics"]["final_value"] < free["metrics"]["final_value"]


# ---------- 여러 종목·결손·경계 ----------


class TestMultiAndEdges:
    def test_two_tickers_equal_split_and_common_calendar(self):
        a = list(zip(D[:5], [100, 100, 110, 120, 130]))
        b = [(d, v) for d, v in zip(D[:5], [50, 50, 40, 30, 20]) if d != D[2]]  # B는 D2가 없다
        out = run({"A": a, "B": b}, "buy_and_hold", cash=10_000)
        assert out["period"]["trading_days"] == 4
        assert [t[3] for t in trade_rows(out)] == [50, 100]  # 각 5,000원: 5,000/100, 5,000/50
        assert values(out) == [10_000, 10_000, 9_000, 8_500]  # D3: 50×120 + 100×30, D4: 50×130 + 100×20
        assert any("1거래일은 공통 거래일에서 뺐습니다" in n for n in out["notices"])
        assert [p["return_pct"] for p in out["per_ticker"]] == [30.0, -60.0]

    def test_period_is_adjusted_and_said_so(self):
        out = run({"A": list(zip(D[:5], [1, 2, 3, 4, 5]))}, "buy_and_hold", start=date(2025, 1, 1), end=date(2026, 6, 1))
        assert out["period"]["adjusted"] is True
        assert (out["period"]["start"], out["period"]["end"]) == ("2026-01-05", "2026-01-09")
        assert out["period"]["requested_start"] == "2025-01-01"
        assert any("2026-01-05~2026-01-09" in n for n in out["notices"])

    def test_too_short_is_an_error_with_reason(self):
        with pytest.raises(BacktestError, match="거래일이 5일이라.*최소 30일"):
            run({"A": list(zip(D[:5], [1, 2, 3, 4, 5]))}, "buy_and_hold", min_days=30)
        with pytest.raises(BacktestError, match="시세 없음"):
            run_backtest({"A": []}, {}, "buy_and_hold", {}, D[0], D[-1], 1000)

    def test_no_signal_means_no_trades(self):
        out = run({"A": list(zip(D, [20, 19, 18, 17, 16, 15, 14, 13, 12]))}, "sma_cross", {"short": 2, "long": 3}, cash=1_000)
        assert out["trades"]["total"] == 0 and out["metrics"]["total_return_pct"] == 0.0
        assert any("체결된 거래가 없습니다" in n for n in out["notices"])

    def test_all_down(self):
        """D0 신호 → D1 90원에 100주 → 마지막 20원: 2,000 ÷ 9,000 − 1 = −77.78%."""
        out = run({"A": list(zip(D, [100, 90, 80, 70, 60, 50, 40, 30, 20]))}, "buy_and_hold", cash=9_000)
        m = out["metrics"]
        assert m["total_return_pct"] == -77.78 and m["max_drawdown"]["recovery_date"] is None
        assert m["max_drawdown"]["pct"] == -77.78 and m["sharpe_like"] < 0

    def test_cannot_afford_a_single_share(self):
        out = run({"A": list(zip(D[:4], [5_000, 5_000, 5_000, 5_000]))}, "buy_and_hold", cash=1_000)
        assert out["trades"]["total"] == 0 and out["metrics"]["final_value"] == 1_000

    def test_benchmark_full_and_partial(self):
        prices = {"A": list(zip(D[:5], [100, 100, 110, 120, 130]))}
        full = run(prices, "buy_and_hold", cash=10_000, index=list(zip(D[:5], [1000, 1010, 1020, 1030, 1100])))
        assert full["benchmark"]["return_pct"] == 10.0 and full["benchmark"]["covers_full_period"] is True
        assert full["benchmark"]["excess_return_pct"] == round(full["metrics"]["total_return_pct"] - 10.0, 2)
        part = run(prices, "buy_and_hold", cash=10_000, index=list(zip(D[2:4], [1000, 1050])))
        assert part["benchmark"]["covers_full_period"] is False and part["benchmark"]["return_pct"] == 5.0
        assert any("만 있어" in n for n in part["notices"])
        none = run(prices, "buy_and_hold", cash=10_000)
        assert none["benchmark"] is None

    def test_data_warning_for_discontinuity(self):
        out = run({"A": list(zip(D[:5], [100, 100, 20, 21, 22]))}, "buy_and_hold", cash=10_000)
        w = out["data_warnings"]
        assert len(w) == 1 and w[0]["kind"] == "jump" and w[0]["date"] == "2026-01-07" and w[0]["value"] == -80.0

    def test_cagr_volatility_sharpe(self):
        days = days_from(date(2024, 1, 1), 300)
        closes = [100 * (1.001 ** i) for i in range(300)]
        out = run({"A": list(zip(days, closes))}, "buy_and_hold", cash=1_000_000)
        m = out["metrics"]
        span = (days[-1] - days[1]).days
        assert m["cagr_pct"] is not None and m["annualized_volatility_pct"] is not None
        assert m["sharpe_like"] > 10  # 매일 0.1%씩 일정하게 오르면 변동성이 아주 작다
        assert span > 0

    def test_curve_sampling_and_trade_cap(self):
        days = days_from(date(2018, 1, 1), 1300)
        closes = _random_closes(1300, 11)
        out = run({"A": list(zip(days, closes))}, "sma_cross", {"short": 2, "long": 5}, cash=1_000_000, max_trades=10)
        c = out["curve"]
        assert c["total_points"] == 1300 and c["sampled"] is True and len(c["points"]) <= 400
        assert c["points"][0]["date"] == days[0].isoformat() and c["points"][-1]["date"] == days[-1].isoformat()
        t = out["trades"]
        assert t["total"] > 10 and len(t["items"]) == 10 and t["truncated"] is True


# ---------- 파라미터 검증 ----------


class TestParams:
    def test_defaults(self):
        assert validate_params("sma_cross", {}, 1e7) == {"short": 20, "long": 60}
        assert validate_params("dca", {}, 1e7) == {"monthly_amount": 1_000_000}
        assert validate_params("dca", {}, 500_000) == {"monthly_amount": 500_000}
        assert validate_params("buy_and_hold", None, 1e7) == {}

    @pytest.mark.parametrize("strategy, params, field", [
        ("sma_cross", {"short": 1}, "short"), ("sma_cross", {"short": 121}, "short"), ("sma_cross", {"long": 2}, "long"),
        ("sma_cross", {"long": 251}, "long"), ("sma_cross", {"short": 30, "long": 30}, "short"),
        ("sma_cross", {"short": 2.5}, "short"), ("sma_cross", {"short": True}, "short"), ("sma_cross", {"short": "5"}, "short"),
        ("sma_cross", {"window": 5}, "window"), ("dca", {"monthly_amount": 9_999}, "monthly_amount"),
        ("dca", {"monthly_amount": 1e8}, "monthly_amount"), ("dca", {"monthly_amount": float("nan")}, "monthly_amount"),
        ("buy_and_hold", {"short": 5}, "short"), ("momentum", {}, "strategy"),
    ])
    def test_invalid(self, strategy, params, field):
        with pytest.raises(ParamError) as e:
            validate_params(strategy, params, 1e7)
        assert e.value.field == field

    def test_engine_level_checks(self):
        prices = {"A": list(zip(D, range(1, 10)))}
        with pytest.raises(ParamError):
            run_backtest(prices, {}, "buy_and_hold", {}, D[0], D[-1], 1000, fee_rate=0.02)
        with pytest.raises(ParamError):
            run_backtest({t: prices["A"] for t in "ABCDEF"}, {}, "buy_and_hold", {}, D[0], D[-1], 1000)
        with pytest.raises(ParamError):
            run_backtest(prices, {}, "buy_and_hold", {}, D[-1], D[0], 1000)


# ---------- 금지 표현 ----------


class TestForbidden:
    def test_all_texts(self):
        prices = {"A": list(zip(D[:5], [100, 100, 20, 21, 22])), "B": list(zip(D[:4], [1, 1, 1, 1]))}
        outs = [
            run(prices, "buy_and_hold", cash=10_000, start=date(2025, 1, 1), index=list(zip(D[1:3], [1, 2]))),
            run({"A": list(zip(D, [20, 19, 18, 17, 16, 15, 14, 13, 12]))}, "sma_cross", {"short": 2, "long": 3}),
            run({"A": list(zip(TestDcaByHand.DAYS, TestDcaByHand.CLOSES))}, "dca", {"monthly_amount": 60_000}, cash=100_000),
            run({"A": list(zip(D[:3], [1, 2, 3]))}, "buy_and_hold", index=[(date(2020, 1, 1), 1.0)]),
        ]
        texts = list(ASSUMPTIONS) + [PAST_PERFORMANCE, DISCLAIMER]
        for o in outs:
            texts += o["notices"] + [w["detail"] for w in o["data_warnings"]]
        for exc in (lambda: run({"A": list(zip(D[:5], [1] * 5))}, "buy_and_hold", min_days=30),):
            with pytest.raises(BacktestError) as e:
                exc()
            texts.append(str(e.value))
        assert len(texts) >= 15
        for t in texts:
            assert forbidden_hits(t) == [], t


# ---------- 라우터(mock) ----------


GOOD = {"tickers": ["005930"], "strategy": "buy_and_hold", "start": "2024-01-02", "end": "2026-10-28"}
FIELDS = {"generated_at", "input", "period", "metrics", "per_ticker", "benchmark", "curve", "trades", "assumptions",
          "data_warnings", "notices", "disclaimer"}


class _Company:
    def __init__(self, cid, ticker, name):
        self.id, self.ticker, self.name = cid, ticker, name


class _Res:
    def __init__(self, company=None, message=None):
        self.company, self.message = company, message


@pytest.fixture
def fake(monkeypatch):
    """SessionLocal·종목 해석·시세 조회를 가짜로 바꾼다. 005930/000660만 있다."""
    known = {"005930": _Company(1, "005930", "삼성전자"), "000660": _Company(2, "000660", "SK하이닉스")}
    monkeypatch.setattr(backtest_api, "SessionLocal", lambda: type("S", (), {"close": lambda self: None})())

    def resolve(db, raw, allow_fuzzy=True):
        assert allow_fuzzy is False  # 엄격 모드
        if raw in known:
            return _Res(known[raw])
        return _Res(message="'삼성전쟈' 종목을 찾지 못했습니다. 혹시 삼성전자 중 하나인가요?")

    days = days_from(date(2024, 1, 2), 400)
    monkeypatch.setattr(backtest_api, "resolve_company", resolve)
    monkeypatch.setattr(backtest_db, "load_prices", lambda db, companies, start, end, now: {
        "prices": {c.ticker: [(d, 100 + i * (c.id)) for i, d in enumerate(days) if start <= d <= end] for c in companies},
        "index": [(d, 1000 + i) for i, d in enumerate(days)],
    })
    return known


class TestRouter:
    def test_401_before_validation(self):
        assert client.post("/api/backtest", json={}).status_code == 401
        assert client.post("/api/backtest", json=GOOD).status_code == 401

    def test_contract(self, login_as, fake):
        login_as(5)
        r = client.post("/api/backtest", json={**GOOD, "tickers": ["005930", "000660"], "strategy": "sma_cross",
                                               "params": {"short": 5, "long": 20}})
        assert r.status_code == 200, r.text
        body = r.json()
        assert set(body) == FIELDS
        assert body["disclaimer"] == f"{DISCLAIMER} {PAST_PERFORMANCE}"
        assert body["input"]["params"] == {"short": 5, "long": 20}
        assert body["input"]["fee_rate"] == 0.00015 and body["input"]["sell_tax_rate"] == 0.0018
        assert set(body["metrics"]) == {"final_value", "total_return_pct", "cagr_pct", "max_drawdown", "annualized_volatility_pct",
                                        "sharpe_like", "trade_count", "buy_count", "sell_count", "fees_paid", "taxes_paid",
                                        "ending_cash"}
        assert set(body["period"]) == {"requested_start", "requested_end", "start", "end", "trading_days", "adjusted"}
        assert set(body["trades"]) == {"items", "total", "truncated", "max_items"}
        assert body["benchmark"]["index_code"] == "KS11"
        assert [t["ticker"] for t in body["input"]["tickers"]] == ["005930", "000660"]

    def test_unknown_ticker_is_404_with_candidates(self, login_as, fake):
        login_as(5)
        r = client.post("/api/backtest", json={**GOOD, "tickers": ["005930", "삼성전쟈"]})
        assert r.status_code == 404 and "혹시 삼성전자" in r.json()["detail"]

    @pytest.mark.parametrize("patch", [
        {"tickers": []}, {"tickers": ["a", "b", "c", "d", "e", "f"]}, {"tickers": ["005930", "005930"]}, {"tickers": [""]},
        {"tickers": ["x" * 51]}, {"strategy": "momentum"}, {"start": "2026-10-28"}, {"start": "2020-01-01"},
        {"start": "1989-12-31", "end": "1994-01-01"}, {"end": "2999-01-01"}, {"start": "2026-13-01"},
        {"initial_cash": 99_999}, {"initial_cash": 10_000_000_001}, {"fee_rate": -0.0001}, {"fee_rate": 0.0101},
        {"sell_tax_rate": 0.02}, {"initial_cash": "abc"},
    ])
    def test_422_boundaries(self, login_as, fake, patch):
        login_as(5)
        assert client.post("/api/backtest", json={**GOOD, **patch}).status_code == 422

    @pytest.mark.parametrize("params, loc", [
        ({"short": 30, "long": 20}, ["body", "params", "short"]), ({"long": 300}, ["body", "params", "long"]),
        ({"foo": 1}, ["body", "params", "foo"]),
    ])
    def test_param_errors_are_422_with_location(self, login_as, fake, params, loc):
        login_as(5)
        r = client.post("/api/backtest", json={**GOOD, "strategy": "sma_cross", "params": params})
        assert r.status_code == 422 and r.json()["detail"][0]["loc"] == loc

    def test_boundaries_that_pass(self, login_as, fake):
        login_as(5)
        ok = {**GOOD, "start": "2021-10-28", "initial_cash": 100_000, "fee_rate": 0.01, "sell_tax_rate": 0}
        assert client.post("/api/backtest", json=ok).status_code == 200

    def test_short_data_is_400_with_reason(self, login_as, fake):
        login_as(5)
        r = client.post("/api/backtest", json={**GOOD, "start": "2024-01-02", "end": "2024-01-20"})
        assert r.status_code == 400 and "최소 30일" in r.json()["detail"]

    def test_rate_limit(self, login_as, fake, monkeypatch):
        monkeypatch.setenv("RATE_LIMIT_ENABLED", "1")
        monkeypatch.setenv("RATE_LIMIT_BACKTEST_PER_MIN", "2")
        login_as(5)
        codes = [client.post("/api/backtest", json=GOOD).status_code for _ in range(3)]
        assert codes == [200, 200, 429]
        r = client.post("/api/backtest", json=GOOD)
        assert r.headers["retry-after"] and "요청이 너무 많습니다" in r.json()["detail"]
        login_as(6)
        assert client.post("/api/backtest", json=GOOD).status_code == 200  # 사용자별로 센다
        assert RL.DEFAULTS["RATE_LIMIT_BACKTEST_PER_MIN"] == 10
