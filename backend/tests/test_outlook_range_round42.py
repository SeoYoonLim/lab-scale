"""2026-11-11(42차): "최근 N거래일" 최고·최저 종가의 정의를 답변 경로 사이에서 하나로.

전망(app/outlook) 경로는 N+1개 종가(수익률의 비교 기준일 종가까지)에서 최고·최저를 골라, 같은 "최근 20거래일"인데 주가 흐름
(app/price_flow, 35차에 고친 정의) 답과 값이 달랐다. 실제 DB(2026-11-03)에서 1주일 기간 350종목 중 91종목이 달랐다.
예: SK하이닉스 전망 "최근 20거래일 최고 1,842,000원(10-02)"(10-02는 21번째 종가) vs 주가 흐름 "최고 1,784,000원(10-06)".
"""
import re
from datetime import date, timedelta

import pytest

from app import outlook, price_flow


def _rows(closes: list[float]) -> list[tuple]:
    d, out = date(2025, 1, 2), []
    for c in closes:
        while d.weekday() >= 5:
            d += timedelta(days=1)
        out.append((d, c, 1_000_000))
        d += timedelta(days=1)
    return out


def _baseline_is_extreme(n_total: int, n: int, kind: str) -> list[tuple]:
    """비교 기준일(뒤에서 n+1번째) 종가만 극값인 시세."""
    closes = [100.0 + (i % 7) for i in range(n_total)]
    closes[-1 - n] = 1000.0 if kind == "high" else 1.0
    return _rows(closes)


@pytest.mark.parametrize("label", list(outlook.PERIODS))
@pytest.mark.parametrize("kind", ["high", "low"])
def test_outlook_range_excludes_the_comparison_base_close(label, kind):
    period = outlook.PERIODS[label]
    n = period.range_days
    rows = _baseline_is_extreme(300, n, kind)
    ind = outlook.compute_indicators(rows, period)
    rg = ind["range"]
    inside = [c for _, c, _ in rows[-n:]]
    assert rg["days"] == n and rg["from_date"] == rows[-n][0].isoformat() and rg["to_date"] == rows[-1][0].isoformat()
    assert rg["high"] == max(inside) and rg["low"] == min(inside)
    assert rg[kind] != (1000.0 if kind == "high" else 1.0)  # 기준일 종가는 최고·최저가 아니다(수익률에만 쓴다)
    # 같은 N의 주가 흐름 답과 값·날짜가 같다
    pf = price_flow.compute(rows, {"returns": (1,), "mas": (5,), "volume_avg": 20, "window": n})["window"]
    assert (rg["high"], rg["high_date"], rg["low"], rg["low_date"]) == (pf["high"], pf["high_date"], pf["low"], pf["low_date"])
    # 수익률은 그대로 비교 기준일 종가 대비다
    assert ind["returns"][period.days]["from_date"] == rows[-1 - period.days][0].isoformat()


def test_52_week_range_is_the_last_250_closes():
    rows = _baseline_is_extreme(300, outlook.RANGE52_DAYS, "low")
    r52 = outlook.compute_indicators(rows, outlook.PERIODS["1주일"])["range52"]
    assert r52["days"] == outlook.RANGE52_DAYS and r52["from_date"] == rows[-outlook.RANGE52_DAYS][0].isoformat()
    assert r52["low"] == min(c for _, c, _ in rows[-outlook.RANGE52_DAYS:]) and r52["low"] != 1.0


def test_regime_low20_is_the_last_20_closes():
    rows = _baseline_is_extreme(300, 20, "low")
    ri = outlook.compute_indicators(rows, outlook.PERIODS["1주일"])["regime_inputs"]
    close = rows[-1][1]
    assert ri["from_low20_pct"] == outlook._pct(close, min(c for _, c, _ in rows[-20:]))


def test_stated_window_matches_the_dates_it_names():
    """답 문장의 "조회 기간 a~b, 최근 N거래일"에서 a~b 사이 종가 수가 정확히 N이다(전에는 N+1)."""
    period = outlook.PERIODS["1주일"]
    rows = _baseline_is_extreme(300, period.range_days, "high")
    ind = outlook.compute_indicators(rows, period)
    text = "\n".join(outlook.indicator_lines(ind, period))
    found = re.findall(r"조회 기간 (\S+?)~(\S+?), (?:분석 기간|52주) 기준 최근 (\d+)거래일", text)
    assert len(found) == 4
    for a, b, n in found:
        assert sum(1 for d, _, _ in rows if a <= d.isoformat() <= b) == int(n)
