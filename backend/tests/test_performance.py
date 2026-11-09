"""성과 추적 순수 계산(app/performance.py)과 라우터(mock). DB 없음. 실제 DB 대조는 test_performance_devdb.py.

손계산 예제의 기대값은 아래 주석의 계산을 손으로 한 결과다(초기 자금 1,000,000원으로 줄여서 계산을 쉽게 했다).
"""

from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

import app.api.performance as performance_api
import app.performance_db as performance_db
from app.disclaimer import DISCLAIMER
from app.main import app
from app.performance import (
    MAX_POINTS,
    MSG_NO_PRICES,
    MSG_NO_TRADES,
    MSG_ONLY_PENDING,
    TradeIn,
    compute_performance,
    max_drawdown,
    months_before,
    pct,
    sample_points,
    volatility,
)
from app.performance_db import CASH_MISMATCH, replayed_cash
from evals.metrics import forbidden_hits

client = TestClient(app, raise_server_exceptions=False)

INIT = 1_000_000
D = [date(2026, 1, 5) + timedelta(days=i) for i in range(5)]  # 월~금


def buy(day, ticker, qty, price, name=None):
    return TradeIn(day, ticker, name or ticker, "buy", qty, price)


def sell(day, ticker, qty, price, name=None):
    return TradeIn(day, ticker, name or ticker, "sell", qty, price)


def series(days, values):
    return [(d, v) for d, v in zip(days, values) if v is not None]


def curve_values(out):
    return [(p["date"], p["total_asset"]) for p in out["curve"]["points"]]


# ---------- 손계산 예제 ----------


class TestExample1SingleBuy:
    """D1~D5, A 종가 100/110/99/108/120. D2에 A 4,000주 @105 매수(420,000원) → 현금 580,000.
    자산: D1 1,000,000 · D2 580,000+440,000=1,020,000 · D3 976,000 · D4 1,012,000 · D5 1,060,000.
    MDD: D2 고점 → D3 저점 976,000/1,020,000−1 = −4.31%, D4(1,012,000)는 아직 아래, D5에 회복.
    일간 수익률: 2.0, −4.313725, 3.688525, 4.743083 → 표본 표준편차 4.0560%.
    지수 1000 → 1100(+10%) 대비 초과 6 − 10 = −4.00%p."""

    @pytest.fixture
    def out(self):
        closes = {"A": series(D, [100, 110, 99, 108, 120])}
        index = series(D, [1000, 1010, 990, 1050, 1100])
        return compute_performance([buy(D[1], "A", 4000, 105)], closes, D, index, "all", INIT)

    def test_curve(self, out):
        assert curve_values(out) == [
            ("2026-01-05", 1_000_000), ("2026-01-06", 1_020_000), ("2026-01-07", 976_000),
            ("2026-01-08", 1_012_000), ("2026-01-09", 1_060_000),
        ]
        assert out["curve"]["points"][-1] == {"date": "2026-01-09", "total_asset": 1_060_000, "cash_balance": 580_000,
                                              "eval_amount": 480_000, "return_pct": 6.0}

    def test_returns_and_cash(self, out):
        s = out["summary"]
        assert s["cumulative_return_pct"] == s["period_return_pct"] == 6.0
        assert s["profit_loss"] == 60_000 and s["start_value"] == INIT
        assert s["cash_weight_pct"] == 54.72  # 580,000 / 1,060,000

    def test_mdd(self, out):
        assert out["risk"]["max_drawdown"] == {"pct": -4.31, "peak_date": "2026-01-06", "trough_date": "2026-01-07",
                                               "recovery_date": "2026-01-09", "duration_days": 1}

    def test_volatility(self, out):
        assert out["risk"]["daily_return_count"] == 4
        assert out["risk"]["daily_volatility_pct"] == 4.056
        assert out["risk"]["annualized_volatility_pct"] == 64.3868  # × √252

    def test_contribution_and_benchmark(self, out):
        assert out["contributions"] == [{
            "ticker": "A", "company_name": "A", "profit_loss": 60_000, "contribution_pct": 6.0, "quantity": 4000,
            "eval_amount": 480_000, "unrealized_profit_loss": 60_000, "realized_profit_loss": 0,
        }]
        assert out["benchmark"] == {"index_code": "KS11", "index_name": "코스피", "start_date": "2026-01-05",
                                    "end_date": "2026-01-09", "index_return_pct": 10.0, "portfolio_return_pct": 6.0,
                                    "excess_return_pct": -4.0, "covers_full_period": True}
        assert out["trades"]["win_rate_pct"] is None  # 매도 없음
        assert out["notices"] == [] and out["data_gaps"] == []


class TestExample2SameDayOrdersAndSells:
    """D0~D4. A 100/100/120/130/90, B 50/50/40/45/60.
    D1: A 10@100, B 20@50, A 10@110(같은 날) → 현금 996,900, A 20주 평균 105, B 20주 평균 50 → 자산 999,900.
    D2: A 10@120 매도 → 실현 (120−105)×10 = +150, 현금 998,100 → 998,100+1,200+800 = 1,000,100.
    D3: B 20@45 매도 → 실현 (45−50)×20 = −100, 현금 999,000 → 999,000+1,300 = 1,000,300.
    D4: 999,000+900 = 999,900. MDD: D3 → D4 999,900/1,000,300−1 = −0.04%.
    기여도: A = 900 + 1,200 − 2,100 = 0, B = 0 + 900 − 1,000 = −100(합 −100 = 기간 손익)."""

    @pytest.fixture
    def out(self):
        closes = {"A": series(D, [100, 100, 120, 130, 90]), "B": series(D, [50, 50, 40, 45, 60])}
        trades = [buy(D[1], "A", 10, 100), buy(D[1], "B", 20, 50), buy(D[1], "A", 10, 110),
                  sell(D[2], "A", 10, 120), sell(D[3], "B", 20, 45)]
        return compute_performance(trades, closes, D, [], "all", INIT)

    def test_curve(self, out):
        assert [v for _, v in curve_values(out)] == [1_000_000, 999_900, 1_000_100, 1_000_300, 999_900]

    def test_trades(self, out):
        assert out["trades"] == {"count": 5, "buys": 3, "sells": 2, "wins": 1, "losses": 1, "even": 0,
                                 "win_rate_pct": 50.0, "realized_profit_loss": 50.0, "pending_count": 0}

    def test_mdd_and_vol(self, out):
        mdd = out["risk"]["max_drawdown"]
        assert (mdd["pct"], mdd["peak_date"], mdd["trough_date"], mdd["recovery_date"]) == (-0.04, "2026-01-08", "2026-01-09", None)
        assert out["risk"]["daily_volatility_pct"] == 0.0287

    def test_contributions_sum_to_period_pnl(self, out):
        c = {x["ticker"]: x for x in out["contributions"]}
        assert [x["ticker"] for x in out["contributions"]] == ["A", "B"]  # 손익 큰 순
        assert (c["A"]["profit_loss"], c["A"]["unrealized_profit_loss"], c["A"]["realized_profit_loss"]) == (0, -150, 150)
        assert (c["B"]["profit_loss"], c["B"]["unrealized_profit_loss"], c["B"]["quantity"]) == (-100, None, 0)
        assert sum(x["profit_loss"] for x in out["contributions"]) == out["summary"]["profit_loss"] == -100
        assert out["summary"]["cash_weight_pct"] == 99.91

    def test_no_index_is_a_notice_not_an_error(self, out):
        assert out["benchmark"] is None
        assert any("지수 데이터가 없어" in n for n in out["notices"])


class TestExample3PeriodGapAndAllSold:
    """달력 02-02, 02-27, 03-02, 03-03, 03-04, 03-31. C 종가 990(02-02), 1,000(03-02), 1,100(03-03), 03-04·03-31 없음.
    03-02 C 100@1,000 매수 → 현금 900,000. 03-31 전량 100@1,200 매도 → 현금 1,020,000(현금만).
    1m: 03-31 − 1개월 = 02-28 → 그 이전 마지막 거래일 02-27이 시작(1,000,000).
    자산 02-27 1,000,000 · 03-02 1,000,000 · 03-03 1,010,000 · 03-04 1,010,000(직전 종가 1,100, data_gaps) · 03-31 1,020,000.
    일간 수익률 0, 1, 0, 0.990099 → 표준편차 0.5745%. 지수 03-02 2000 → 03-31 2100(+5%), 같은 구간 포트폴리오 +2% → −3%p."""

    CAL = [date(2026, 2, 2), date(2026, 2, 27), date(2026, 3, 2), date(2026, 3, 3), date(2026, 3, 4), date(2026, 3, 31)]
    CLOSES = {"C": [(date(2026, 2, 2), 990), (date(2026, 3, 2), 1000), (date(2026, 3, 3), 1100)]}
    TRADES = [buy(date(2026, 3, 2), "C", 100, 1000, "씨"), sell(date(2026, 3, 31), "C", 100, 1200, "씨")]
    INDEX = [(date(2026, 3, 2), 2000), (date(2026, 3, 31), 2100)]

    def test_1m(self):
        out = compute_performance(self.TRADES, self.CLOSES, self.CAL, self.INDEX, "1m", INIT)
        assert out["start_date"] == "2026-02-27" and out["end_date"] == "2026-03-31"
        assert [v for _, v in curve_values(out)] == [1_000_000, 1_000_000, 1_010_000, 1_010_000, 1_020_000]
        assert out["summary"]["period_return_pct"] == out["summary"]["cumulative_return_pct"] == 2.0
        assert out["summary"]["cash_weight_pct"] == 100.0 and out["summary"]["eval_amount"] == 0
        assert out["risk"]["daily_volatility_pct"] == 0.5745
        assert out["risk"]["max_drawdown"]["pct"] == 0.0 and out["risk"]["max_drawdown"]["peak_date"] is None
        assert out["data_gaps"] == [{"ticker": "C", "company_name": "씨", "kind": "previous_close", "days": 1,
                                     "first_date": "2026-03-04", "last_date": "2026-03-04"}]
        assert out["trades"]["win_rate_pct"] == 100.0 and out["trades"]["realized_profit_loss"] == 20_000
        assert out["contributions"][0]["profit_loss"] == 20_000 and out["contributions"][0]["contribution_pct"] == 2.0
        b = out["benchmark"]
        assert (b["start_date"], b["index_return_pct"], b["portfolio_return_pct"], b["excess_return_pct"], b["covers_full_period"]) == (
            "2026-03-02", 5.0, 2.0, -3.0, False)
        assert any("2026-03-02~2026-03-31만" in n for n in out["notices"])

    def test_3m_starts_at_first_calendar_day_with_flat_initial_segment(self):
        out = compute_performance(self.TRADES, self.CLOSES, self.CAL, self.INDEX, "3m", INIT)
        assert out["start_date"] == "2026-02-02"
        assert curve_values(out)[:2] == [("2026-02-02", 1_000_000), ("2026-02-27", 1_000_000)]
        assert any("첫 체결일(2026-03-02) 전 구간은 초기 자금 그대로" in n for n in out["notices"])

    def test_period_start_value_is_the_value_at_the_start_day(self):
        """6일 중 03-03부터 보면(달력을 잘라 1m 시작이 03-03이 되게) 시작값은 1,010,000, 보유 평가액이 기여도 시작값이다."""
        cal = [date(2026, 3, 3), date(2026, 3, 4), date(2026, 4, 3)]
        closes = {"C": [(date(2026, 3, 2), 1000), (date(2026, 3, 3), 1100), (date(2026, 4, 3), 1150)]}
        trades = [buy(date(2026, 3, 2), "C", 100, 1000)]
        out = compute_performance(trades, closes, cal, [], "1m", INIT)
        assert out["start_date"] == "2026-03-03" and out["summary"]["start_value"] == 1_010_000
        assert out["summary"]["profit_loss"] == 5_000  # 100 × (1,150 − 1,100)
        assert out["contributions"][0]["profit_loss"] == 5_000
        assert out["trades"]["count"] == 0  # 매수는 기간 시작 전


# ---------- 경계 ----------


class TestEdges:
    def test_no_trades(self):
        out = compute_performance([], {}, D, [], "all", INIT)
        assert out["message"] == MSG_NO_TRADES
        assert out["curve"]["points"] == [{"date": "2026-01-09", "total_asset": INIT, "cash_balance": INIT,
                                           "eval_amount": 0.0, "return_pct": 0.0}]
        assert out["risk"]["daily_volatility_pct"] is None and out["trades"]["win_rate_pct"] is None
        assert out["benchmark"] is None and out["contributions"] == []

    def test_no_prices_at_all(self):
        out = compute_performance([], {}, [], [], "all", INIT)
        assert out["message"] == MSG_NO_TRADES and out["curve"]["points"] == []
        out = compute_performance([buy(D[0], "A", 1, 100)], {}, [], [], "all", INIT)
        assert out["message"] == MSG_NO_PRICES and out["trades"]["pending_count"] == 1

    def test_trades_after_data_date_are_pending(self):
        out = compute_performance([buy(D[4] + timedelta(days=1), "A", 1, 100)], {"A": series(D, [1] * 5)}, D, [], "all", INIT)
        assert out["message"] == MSG_ONLY_PENDING and out["trades"]["pending_count"] == 1
        assert any("1건은 아직 종가가 없어" in n for n in out["notices"])

    def test_some_pending_some_done(self):
        trades = [buy(D[1], "A", 10, 100), buy(D[4] + timedelta(days=3), "A", 10, 100)]
        out = compute_performance(trades, {"A": series(D, [100] * 5)}, D, [], "all", INIT)
        assert out["trades"]["count"] == 1 and out["trades"]["pending_count"] == 1
        assert out["contributions"][0]["quantity"] == 10

    def test_single_day(self):
        """거래일이 하루뿐이고 그날 체결: 시작값은 초기 자금, 변동성 null."""
        out = compute_performance([buy(D[0], "A", 100, 100)], {"A": [(D[0], 110)]}, [D[0]], [], "all", INIT)
        assert curve_values(out) == [("2026-01-05", 1_001_000)]
        assert out["summary"]["period_return_pct"] == 0.1
        assert out["risk"]["daily_volatility_pct"] is None and out["risk"]["daily_return_count"] == 0
        assert out["trades"]["count"] == 1 and out["contributions"][0]["profit_loss"] == 1_000

    def test_weekend_trade_applies_at_next_trading_day(self):
        cal = [date(2026, 1, 9), date(2026, 1, 12)]  # 금, 월
        out = compute_performance([buy(date(2026, 1, 10), "A", 10, 100)], {"A": series(cal, [100, 130])}, cal, [], "all", INIT)
        assert curve_values(out) == [("2026-01-09", INIT), ("2026-01-12", 1_000_300)]

    def test_ticker_without_any_close_uses_trade_price(self):
        out = compute_performance([buy(D[1], "Z", 10, 500)], {}, D, [], "all", INIT)
        assert [v for _, v in curve_values(out)] == [INIT] * 5
        assert out["data_gaps"] == [{"ticker": "Z", "company_name": "Z", "kind": "trade_price", "days": 4,
                                     "first_date": "2026-01-06", "last_date": "2026-01-09"}]

    def test_sell_all_then_cash_only(self):
        trades = [buy(D[1], "A", 10, 100), sell(D[2], "A", 10, 100), buy(D[3], "A", 5, 200)]
        out = compute_performance(trades, {"A": series(D, [100, 100, 100, 200, 210])}, D, [], "all", INIT)
        c = out["contributions"][0]
        assert c["quantity"] == 5 and c["unrealized_profit_loss"] == 50  # 전량 매도 후 새 평균단가 200
        assert out["trades"]["even"] == 1 and out["trades"]["win_rate_pct"] == 0.0

    def test_inconsistent_history_is_skipped_with_notice(self):
        out = compute_performance([sell(D[1], "A", 10, 100)], {"A": series(D, [100] * 5)}, D, [], "all", INIT)
        assert any("재생하지 못한 체결 1건" in n for n in out["notices"])

    def test_invalid_period(self):
        with pytest.raises(ValueError):
            compute_performance([], {}, D, [], "2y", INIT)

    def test_helpers(self):
        assert pct(110, 0) is None and pct(110, -5) is None and pct(110, 100) == pytest.approx(10)
        assert volatility([1.0]) is None and volatility([]) is None
        assert max_drawdown([], [])["pct"] == 0.0
        assert months_before(date(2026, 3, 31), 1) == date(2026, 2, 28)
        assert months_before(date(2024, 3, 31), 1) == date(2024, 2, 29)
        assert months_before(date(2026, 1, 15), 3) == date(2025, 10, 15)

    def test_replayed_cash(self):
        trades = [buy(D[0], "A", 3, 33.335), sell(D[1], "A", 1, 40)]
        assert replayed_cash(trades, INIT) == round(round(INIT - 100.0, 2) + 40, 2)


# ---------- 샘플링 ----------


class TestSampling:
    def _points(self, n):
        return [{"date": f"d{i}", "total_asset": float(1000 + (i % 37) - (500 if i == 777 else 0) + (900 if i == 333 else 0))}
                for i in range(n)]

    def test_under_limit_unchanged(self):
        pts = self._points(10)
        assert sample_points(pts, 400) == pts

    def test_over_limit_keeps_first_last_peak_trough(self):
        pts = self._points(1000)
        out = sample_points(pts, 400, keep=[500])
        assert len(out) <= 400
        dates = [p["date"] for p in out]
        assert dates == sorted(dates, key=lambda d: int(d[1:]))  # 순서 유지
        assert {"d0", "d999", "d333", "d777", "d500"} <= set(dates)

    def test_long_curve_is_sampled_in_response(self):
        cal = [date(2020, 1, 1) + timedelta(days=i) for i in range(900)]
        closes = {"A": [(d, 100 + (i % 50)) for i, d in enumerate(cal)]}
        out = compute_performance([buy(cal[1], "A", 1000, 100)], closes, cal, [], "all", INIT)
        assert out["curve"]["total_points"] == 900 and out["curve"]["sampled"] is True
        pts = out["curve"]["points"]
        assert len(pts) <= MAX_POINTS
        assert pts[0]["date"] == cal[0].isoformat() and pts[-1]["date"] == cal[-1].isoformat()
        mdd = out["risk"]["max_drawdown"]
        assert {mdd["peak_date"], mdd["trough_date"]} <= {p["date"] for p in pts}


# ---------- 금지 표현 ----------


def _all_texts(out) -> list[str]:
    return [t for t in [out.get("message"), out.get("stale_notice"), *out["notices"],
                        out["benchmark"]["index_name"] if out["benchmark"] else None] if t]


class TestForbiddenExpressions:
    def test_every_message_and_notice_is_free_of_forbidden_expressions(self):
        cases = [
            compute_performance([], {}, D, [], "all", INIT),
            compute_performance([], {}, [], [], "all", INIT),
            compute_performance([buy(D[0], "A", 1, 1)], {}, [], [], "all", INIT),
            compute_performance([buy(D[4] + timedelta(days=1), "A", 1, 1)], {}, D, [], "all", INIT),
            compute_performance(TestExample3PeriodGapAndAllSold.TRADES, TestExample3PeriodGapAndAllSold.CLOSES,
                                TestExample3PeriodGapAndAllSold.CAL, TestExample3PeriodGapAndAllSold.INDEX, "3m", INIT),
            compute_performance([sell(D[1], "A", 10, 100), buy(D[1], "Z", 1, 5), buy(D[4] + timedelta(days=1), "A", 1, 1)],
                                {}, D, [(date(2025, 1, 1), 1.0)], "all", INIT),
            compute_performance([buy(D[1], "A", 1, 5)], {}, D, [(D[2], 1.0), (D[3], 1.1)], "all", INIT),
        ]
        texts = [t for out in cases for t in _all_texts(out)]
        texts += [CASH_MISMATCH.format(replayed=1.0, actual=2.0), DISCLAIMER, MSG_NO_TRADES, MSG_NO_PRICES, MSG_ONLY_PENDING]
        assert len(texts) >= 12  # 공허하게 통과하지 않게
        for t in texts:
            assert forbidden_hits(t) == [], t


# ---------- 라우터(mock) ----------


FIELDS = {"generated_at", "period", "data_date", "stale", "stale_notice", "start_date", "end_date", "initial_balance",
          "message", "summary", "risk", "trades", "contributions", "benchmark", "curve", "data_gaps", "notices", "disclaimer"}


@pytest.fixture
def fake_db(monkeypatch):
    """SessionLocal과 load_inputs를 가짜로 바꾸고, 어느 소유자 키로 불렸는지 기록한다."""
    seen = []
    monkeypatch.setattr(performance_api, "SessionLocal", lambda: type("S", (), {"close": lambda self: None})())
    data = {}

    def fake_load(db, owner_key, now):
        seen.append(owner_key)
        return data.get(owner_key, {"cash_balance": None, "trades": [], "closes": {}, "calendar": D, "index_series": []})

    monkeypatch.setattr(performance_db, "load_inputs", fake_load)
    return seen, data


class TestRouter:
    def test_requires_login(self):
        r = client.get("/api/portfolio/performance")
        assert r.status_code == 401

    @pytest.mark.parametrize("q", ["period=2y", "period=", "period=ALL", "period=1M"])
    def test_bad_period_is_422(self, login_as, fake_db, q):
        login_as(7)
        assert client.get(f"/api/portfolio/performance?{q}").status_code == 422
        assert fake_db[0] == []

    def test_empty_user_contract(self, login_as, fake_db):
        login_as(7)
        r = client.get("/api/portfolio/performance")
        assert r.status_code == 200
        body = r.json()
        assert set(body) == FIELDS
        assert body["message"] == MSG_NO_TRADES and body["period"] == "all"
        assert body["disclaimer"] == DISCLAIMER and body["data_date"] == "2026-01-09"
        assert set(body["curve"]) == {"points", "total_points", "sampled", "max_points"}
        assert set(body["risk"]["max_drawdown"]) == {"pct", "peak_date", "trough_date", "recovery_date", "duration_days"}
        assert fake_db[0] == ["user:7"]

    def test_owner_key_is_the_logged_in_user_only(self, login_as, fake_db):
        seen, data = fake_db
        data["user:8"] = {"cash_balance": 580_000, "trades": [buy(D[1], "A", 4000, 105)],
                          "closes": {"A": series(D, [100, 110, 99, 108, 120])}, "calendar": D, "index_series": []}
        login_as(7)
        assert client.get("/api/portfolio/performance").json()["trades"]["count"] == 0
        login_as(8)
        body = client.get("/api/portfolio/performance?period=1m").json()
        assert body["trades"]["count"] == 1 and body["summary"]["end_value"] == 1_060_000 - INIT + 10_000_000
        assert seen == ["user:7", "user:8"]
        assert set(body["contributions"][0]) == {"ticker", "company_name", "profit_loss", "contribution_pct", "quantity",
                                                 "eval_amount", "unrealized_profit_loss", "realized_profit_loss"}

    def test_cash_mismatch_notice(self, login_as, fake_db):
        seen, data = fake_db
        data["user:9"] = {"cash_balance": 123.0, "trades": [buy(D[1], "A", 1, 100)],
                          "closes": {"A": series(D, [100] * 5)}, "calendar": D, "index_series": []}
        login_as(9)
        notices = client.get("/api/portfolio/performance").json()["notices"]
        assert any("현재 현금 잔고" in n for n in notices)

    def test_intraday_today_rows_are_dropped(self, monkeypatch):
        """DB 계층의 장중 규칙: 평일 15:30 전이면 오늘 날짜 행을 버린다(briefing.drop_open_session 재사용)."""
        from app.briefing import KST

        today = date(2026, 1, 9)
        before_close = datetime(2026, 1, 9, 10, 0, tzinfo=KST)
        after_close = datetime(2026, 1, 9, 16, 0, tzinfo=KST)

        class FakeResult:
            def __init__(self, rows):
                self.rows = rows

            def all(self):
                return self.rows

            def __iter__(self):
                return iter(self.rows)

        def make_db():
            calls = []

            class DB:
                def get(self, model, key):
                    return None

                def execute(self, stmt):
                    calls.append(stmt)
                    n = len(calls)
                    if n == 1:
                        return FakeResult([])  # 체결 없음
                    if n == 2:
                        return FakeResult([(d,) for d in D])  # 달력(마지막이 오늘)
                    return FakeResult([(d, 1000.0) for d in D])  # 지수

            return DB(), calls

        db, calls = make_db()
        out = performance_db.get_performance(db, "user:1", "all", now=before_close)
        assert out["data_date"] == "2026-01-08" and len(calls) == 3
        db, _ = make_db()
        assert performance_db.get_performance(db, "user:1", "all", now=after_close)["data_date"] == today.isoformat()
