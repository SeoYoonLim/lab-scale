"""포트폴리오 성과와 시장 지수 비교(GET /api/portfolio/performance?view=benchmark, 2026-11-06). DB·LLM 없이 값만 받아 dict를 만든다.

날짜별 보유 수량·현금 복원과 평가는 기존 app.performance.compute_performance(체결 재생)를 그대로 쓰고(detail=True로 전체 시계열을 받는다),
이 모듈은 그 위에 ① 재생 결과와 실제 계좌의 대조(맞지 않으면 history_unavailable, 곡선을 내지 않는다) ② 데이터 상태
③ 첫 체결 전 구간을 성과에 넣지 않는 기간 보정 ④ 공통 거래일 기준 지수 정규화·초과 수익률 ⑤ 표본이 충분할 때만 내는 지표를 더한다.

규칙
- 총자산 = 현금 + Σ(그날 보유 수량 × 그날 종가). 그날 종가가 없으면 직전 종가(없으면 마지막 체결가)로 평가하고 그 날짜를 estimated로 표시한다.
- 외부 입출금 기능이 없다(현금은 초기 자금, 매수·매도로만 바뀐다. 계좌 초기화는 체결 내역을 지우고 초기 자금으로 되돌린다). 그래서
  수익률은 단순 수익률이고, 입출금이 없을 때 시간가중수익률(TWR)과 같다. 매수·매도는 내부 자산 교체라 수익률에 영향을 주지 않는다.
- 기간 시작이 첫 체결 직전 거래일(inception)보다 이르면 inception부터 계산하고 requested_start_date와 start_date를 둘 다 준다.
- 벤치마크: 포트폴리오 시계열과 지수가 둘 다 있는 거래일만 쓴다. 첫 공통 거래일 값을 100으로 정규화한다. 초과 수익률(%p) = 포트폴리오 − 지수.
- 지표: 최고·최저 총자산, 최대 낙폭은 2개 이상, 변동성은 일간 수익률 MIN_RETURNS_FOR_VOL개 이상, 연환산 수익률은 TRADING_DAYS_PER_YEAR개 이상일 때만.
문장은 app/performance_i18n이 언어별로 붙인다.
"""

from __future__ import annotations

import math
import statistics
from datetime import date

from app.data_status import business_days_behind
from app.models.market_index import MARKET_INDEXES
from app.performance import MAX_POINTS, TRADING_DAYS_PER_YEAR, max_drawdown, pct, sample_points
from app.price_quality import check_series

CALC_VERSION = "performance.v2"
MIN_RETURNS_FOR_VOL = 20
BENCHMARKS = tuple(MARKET_INDEXES)  # ("KS11", "KQ11"): DB에 실제로 있는 지수만
BENCHMARK_ALIASES = {"KOSPI": "KS11", "KOSDAQ": "KQ11", "KS11": "KS11", "KQ11": "KQ11"}

ST_OK, ST_PARTIAL = "ok", "partial"
ST_NO_ACCOUNT, ST_NO_TRADES, ST_PENDING, ST_NO_PRICES, ST_HISTORY = "no_account", "no_trades", "pending_only", "no_prices", "history_unavailable"
STATUSES = (ST_OK, ST_PARTIAL, ST_NO_ACCOUNT, ST_NO_TRADES, ST_PENDING, ST_NO_PRICES, ST_HISTORY)
B_OK, B_PARTIAL, B_NO_DATA, B_INSUFFICIENT, B_UNAVAILABLE = "ok", "partial", "no_data", "insufficient_overlap", "portfolio_unavailable"
BENCHMARK_STATUSES = (B_OK, B_PARTIAL, B_NO_DATA, B_INSUFFICIENT, B_UNAVAILABLE)


def _r(v, n=2):
    return None if v is None else round(v, n)


def _replay_positions(trades) -> dict[str, int]:
    """모든 체결(종가 반영 전 것 포함)을 재생한 보유 수량. 잔량이 음수가 되는 체결은 재생 불가로 본다(None)."""
    pos: dict[str, int] = {}
    for t in trades:
        q = pos.get(t.ticker, 0) + (t.quantity if t.side == "buy" else -t.quantity)
        if q < 0:
            return None
        pos[t.ticker] = q
    return {k: v for k, v in pos.items() if v}


def build(core: dict, trades: list, replayed_cash_all: float | None, actual_cash: float | None, actual_positions: dict[str, int] | None,
          index_series: dict[str, list[tuple[date, float]]], benchmark: str, expected_date: date, closes: dict) -> dict:
    """core = compute_performance(..., detail=True). replayed_cash_all = 모든 체결을 재생한 현금(performance_db.replayed_cash)."""
    detail = core.get("_detail")
    data_date = date.fromisoformat(core["end_date"]) if core.get("end_date") else None
    lag = business_days_behind(data_date, expected_date) if data_date else None
    base = {
        "calc_version": CALC_VERSION, "period": core["period"], "benchmark_code": benchmark, "benchmark_name": MARKET_INDEXES[benchmark],
        "data_date": data_date.isoformat() if data_date else None, "expected_date": expected_date.isoformat(),
        "data_lag_business_days": lag, "initial_balance": core["initial_balance"],
        "cash_flows": {"supported": False, "external_flow_count": 0, "return_method": "simple"},
        "price_basis": {"source": "stock_price.close_price", "dividends_included": False, "corporate_actions_adjusted": "not_verified"},
        "warnings": [],
    }
    warnings = base["warnings"]
    if lag:
        warnings.append({"kind": "data_delayed", "date": base["data_date"], "expected": base["expected_date"], "lag": lag})

    # ---------- 상태(계산할 수 없는 경우) ----------
    status = None
    if actual_cash is None and not trades:
        status = ST_NO_ACCOUNT
    elif data_date is None:
        status = ST_NO_PRICES
    elif not trades:
        status = ST_NO_TRADES
    elif detail is None:
        status = ST_PENDING  # 체결은 있지만 모두 마지막 종가일 뒤
    replay_ok = True
    if trades:
        positions = _replay_positions(trades)
        mismatch = []
        if actual_cash is not None and replayed_cash_all is not None and abs(replayed_cash_all - actual_cash) > 0.01:
            mismatch.append("cash")
        if positions is None or (actual_positions is not None and positions != actual_positions):
            mismatch.append("holdings")
        if detail is not None and detail["skipped"]:
            mismatch.append("unreplayable_trades")
        if mismatch:
            replay_ok = False
            status = ST_HISTORY
            warnings.append({"kind": "history_mismatch", "parts": mismatch,
                             "replayed_cash": _r(replayed_cash_all), "actual_cash": _r(actual_cash)})
    if status is not None:
        if status == ST_PENDING:
            warnings.append({"kind": "pending_trades", "count": core["trades"]["pending_count"], "date": base["data_date"]})
        current = actual_cash if actual_cash is not None else None
        return {**base, "status": status, "requested_start_date": None, "start_date": None, "end_date": base["data_date"],
                "summary": {"current_cash": _r(current), "start_value": None, "end_value": None, "profit_loss": None,
                            "period_return_pct": None, "cumulative_return_pct": None},
                "metrics": _empty_metrics(), "series": {"points": [], "total_points": 0, "sampled": False, "max_points": MAX_POINTS},
                "comparison": {"status": B_UNAVAILABLE, "points": []}, "estimated_dates": [], "history_verified": replay_ok and status != ST_HISTORY}

    # ---------- 기간 보정: 첫 체결 전 구간은 성과에 넣지 않는다 ----------
    series = detail["series"]
    requested_start = series[0]["date"]
    inception = detail["inception"]
    kept = [p for p in series if p["date"] >= inception]
    if requested_start < inception:
        warnings.append({"kind": "period_before_inception", "requested": requested_start.isoformat(), "start": inception.isoformat()})
    # 시작 금액: 전체 기간은 초기 자금, 그 밖은 실제 계산 시작일(보정 후)의 총자산
    start_value = core["initial_balance"] if core["period"] == "all" else kept[0]["total_asset"]

    points = []
    prev = None
    for p in kept:
        v = p["total_asset"]
        points.append({"date": p["date"].isoformat(), "total_asset": v, "cash_balance": p["cash_balance"], "eval_amount": p["eval_amount"],
                       "daily_return_pct": _r(pct(v, prev)) if prev is not None else None,
                       "cumulative_return_pct": _r(pct(v, start_value)), "portfolio_index": _r(v / start_value * 100, 4) if start_value else None,
                       "estimated": bool(p["estimated"]), "estimated_tickers": p["estimated"]})
        prev = v
    estimated_dates = [p["date"] for p in points if p["estimated"]]
    if estimated_dates:
        warnings.append({"kind": "estimated_prices", "count": len(estimated_dates), "first": estimated_dates[0], "last": estimated_dates[-1]})
    status = ST_PARTIAL if estimated_dates else ST_OK

    # 가격 불연속(분할·병합 등 가능성): 보유했던 종목의 종가에서 ±30% 초과 일간 변동(app.price_quality)
    held = {t.ticker for t in trades}
    lo, hi = kept[0]["date"], kept[-1]["date"]
    for t in sorted(held):
        rows = [(d, v) for d, v in closes.get(t, []) if lo <= d <= hi]
        for issue in check_series(rows):
            if issue.kind == "jump":
                warnings.append({"kind": "price_discontinuity", "ticker": t, "date": issue.date.isoformat(), "pct": _r(issue.value)})

    values = [p["total_asset"] for p in points]
    days = [date.fromisoformat(p["date"]) for p in points]
    end_value = values[-1]
    metrics = _metrics(days, values, points)
    sampled = sample_points(points, MAX_POINTS, keep=[i for i, p in enumerate(points) if p["date"] in (
        metrics["max_drawdown"]["peak_date"], metrics["max_drawdown"]["trough_date"])])
    out = {
        **base, "status": status, "history_verified": True,
        "requested_start_date": requested_start.isoformat(), "start_date": points[0]["date"], "end_date": points[-1]["date"],
        "summary": {"current_cash": _r(actual_cash), "start_value": _r(start_value), "end_value": end_value,
                    "profit_loss": _r(end_value - start_value), "period_return_pct": _r(pct(end_value, start_value)),
                    "cumulative_return_pct": core["summary"]["cumulative_return_pct"], "eval_amount": points[-1]["eval_amount"],
                    "cash_balance": points[-1]["cash_balance"]},
        "metrics": metrics,
        "series": {"points": sampled, "total_points": len(points), "sampled": len(sampled) < len(points), "max_points": MAX_POINTS},
        "estimated_dates": estimated_dates,
    }
    out["comparison"] = _compare(points, start_value, index_series.get(benchmark, []), benchmark, expected_date, status, warnings)
    return out


def _empty_metrics() -> dict:
    return {"highest": None, "lowest": None, "max_drawdown": None, "daily_volatility_pct": None, "annualized_volatility_pct": None,
            "annualized_return_pct": None, "daily_return_count": 0, "trading_days": 0,
            "sample_rules": {"volatility_min_returns": MIN_RETURNS_FOR_VOL, "annualized_return_min_returns": TRADING_DAYS_PER_YEAR}}


def _metrics(days: list[date], values: list[float], points: list[dict]) -> dict:
    m = _empty_metrics()
    m["trading_days"] = len(values)
    if not values:
        return m
    hi_i = max(range(len(values)), key=lambda i: (values[i], -i))
    lo_i = min(range(len(values)), key=lambda i: (values[i], i))
    m["highest"] = {"value": values[hi_i], "date": days[hi_i].isoformat()}
    m["lowest"] = {"value": values[lo_i], "date": days[lo_i].isoformat()}
    if len(values) >= 2:
        m["max_drawdown"] = max_drawdown(days, values)
    rets = [p["daily_return_pct"] for p in points[1:] if p["daily_return_pct"] is not None]
    m["daily_return_count"] = len(rets)
    if len(rets) >= MIN_RETURNS_FOR_VOL:
        vol = statistics.stdev(rets)
        m["daily_volatility_pct"] = _r(vol, 4)
        m["annualized_volatility_pct"] = _r(vol * math.sqrt(TRADING_DAYS_PER_YEAR), 4)
    if len(rets) >= TRADING_DAYS_PER_YEAR and values[0] > 0:
        m["annualized_return_pct"] = _r(((values[-1] / values[0]) ** (TRADING_DAYS_PER_YEAR / len(rets)) - 1) * 100)
    return m


def _compare(points: list[dict], start_value: float, idx: list[tuple[date, float]], code: str, expected_date: date, status: str,
             warnings: list) -> dict:
    name = MARKET_INDEXES[code]
    base = {"benchmark_code": code, "benchmark_name": name}
    if not idx:
        warnings.append({"kind": "benchmark_no_data", "code": code})
        return {**base, "status": B_NO_DATA, "points": []}
    by_day = {d.isoformat(): v for d, v in idx if v and v > 0}
    common = [p for p in points if p["date"] in by_day]
    if len(common) < 2:
        warnings.append({"kind": "benchmark_insufficient_overlap", "code": code, "common_days": len(common)})
        return {**base, "status": B_INSUFFICIENT, "common_days": len(common), "points": []}
    s0, b0 = common[0], by_day[common[0]["date"]]
    # 첫 공통 거래일이 포트폴리오 시작일이 아니면 그날을 양쪽의 시작으로 다시 잡는다(시작일 통일)
    p0 = start_value if s0["date"] == points[0]["date"] else s0["total_asset"]
    rows = []
    for p in common:
        pr, br = pct(p["total_asset"], p0), pct(by_day[p["date"]], b0)
        rows.append({"date": p["date"], "portfolio_index": _r(p["total_asset"] / p0 * 100, 4), "benchmark_index": _r(by_day[p["date"]] / b0 * 100, 4),
                     "benchmark_close": by_day[p["date"]], "portfolio_return_pct": _r(pr), "benchmark_return_pct": _r(br),
                     "excess_return_pp": _r(pr - br) if pr is not None and br is not None else None, "estimated": p["estimated"]})
    last_idx = max(d for d, _ in idx)
    idx_lag = business_days_behind(last_idx, expected_date)
    missing = [p["date"] for p in points if p["date"] not in by_day]
    aligned = common[0]["date"] != points[0]["date"]
    st = B_PARTIAL if (missing or status == ST_PARTIAL) else B_OK
    if missing:
        warnings.append({"kind": "benchmark_missing_days", "code": code, "count": len(missing), "first": missing[0], "last": missing[-1]})
    if aligned:
        warnings.append({"kind": "benchmark_start_aligned", "code": code, "start": common[0]["date"]})
    end = rows[-1]
    sampled = sample_points(rows, MAX_POINTS, key="portfolio_index")
    return {**base, "status": st, "start_date": common[0]["date"], "end_date": end["date"], "common_days": len(common),
            "benchmark_start_value": b0, "benchmark_end_value": by_day[end["date"]], "benchmark_last_date": last_idx.isoformat(),
            "benchmark_lag_business_days": idx_lag,
            "portfolio_return_pct": end["portfolio_return_pct"], "benchmark_return_pct": end["benchmark_return_pct"],
            "excess_return_pp": end["excess_return_pp"], "start_aligned": aligned,
            "points": sampled, "total_points": len(rows), "sampled": len(sampled) < len(rows)}
