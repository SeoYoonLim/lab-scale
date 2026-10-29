"""백테스트 엔진(순수 계산: DB·Ollama·LLM 없음). 입력은 종목별 (날짜, 종가) 목록과 지수 목록, 출력은 자산 곡선·거래 목록·지표.

룩어헤드 방지(가장 중요한 규칙)
- 거래일 t의 신호는 t일 종가까지의 값만으로 계산한다(신호 함수는 closes[:t+1]만 받는다).
- 체결은 그 다음 거래일 t+1의 종가로 한다. 마지막 거래일의 신호는 체결되지 않는다.
- 종목이 여러 개면 모든 종목의 종가가 있는 날만 거래일로 쓴다(공통 달력). 그래서 t+1은 모든 종목에 같은 날이다.

체결·비용 규칙
- 정수 주식 수만 체결한다. 매수 수량 = floor(쓸 금액 ÷ (체결가 × (1 + 수수료율))). 남는 현금은 그대로 현금으로 둔다(이자 없음).
- 매수 비용 = 수량 × 체결가 × (1 + 수수료율). 매도 수령액 = 수량 × 체결가 × (1 − 수수료율 − 매도세율).
- 수수료율·매도세율은 요청 값이다. 기본값(DEFAULT_FEE_RATE, DEFAULT_SELL_TAX_RATE)은 가정값이며 실제 수수료·세율과 다를 수 있다.
- 기간 끝에 강제로 팔지 않는다. 마지막 날 자산은 보유 수량 × 마지막 종가 + 현금이다(매도 비용 미반영).
- 여러 종목은 초기 자금을 종목 수로 똑같이 나눈 칸(sleeve)마다 따로 운용한다. 칸 사이에 현금을 옮기지 않고 리밸런싱하지 않는다.

전략
- buy_and_hold: 첫 거래일에 매수 신호 → 다음 거래일 종가에 칸의 현금으로 살 수 있는 만큼 매수, 끝까지 보유.
- sma_cross(short, long): t일에 단기 SMA > 장기 SMA이면 목표 "보유", 아니면 "현금". 목표가 현재 상태와 다르면 다음 거래일에
  전량 매수(칸의 현금 전부) 또는 전량 매도한다. 장기 SMA를 계산할 수 있는 날(long번째 거래일)부터 판단한다.
- dca(monthly_amount): 달마다 그 달의 첫 거래일에 매수 신호 → 다음 거래일 종가에 monthly_amount ÷ 종목 수만큼(칸의 현금이 모자라면
  남은 현금만큼) 매수. 1주도 못 사면 건너뛰고 notice로 센다. 쓰지 않은 현금은 현금으로 남는다.
"""

import math
import statistics
from dataclasses import dataclass
from datetime import date

from app.performance import max_drawdown, pct, sample_points
from app.price_quality import check_series

STRATEGIES = ("buy_and_hold", "sma_cross", "dca")
DEFAULT_FEE_RATE = 0.00015  # 가정값(0.015%). 실제 수수료와 다를 수 있다
DEFAULT_SELL_TAX_RATE = 0.0018  # 가정값(0.18%). 실제 세율과 다를 수 있다
MAX_RATE = 0.01
SMA_SHORT_RANGE = (2, 120)
SMA_LONG_RANGE = (3, 250)
SMA_DEFAULTS = {"short": 20, "long": 60}
DCA_DEFAULT_AMOUNT = 1_000_000
DCA_MIN_AMOUNT = 10_000
MIN_TRADING_DAYS = 30
MAX_TICKERS = 5
MAX_POINTS = 400
MAX_TRADES = 500
TRADING_DAYS_PER_YEAR = 252
BENCHMARK_CODE = "KS11"
BENCHMARK_NAME = "코스피"
PAST_PERFORMANCE = "백테스트 결과는 과거 데이터로 계산한 가상의 성과이며 미래 수익을 보장하지 않습니다."

ASSUMPTIONS = [
    "신호는 그날 종가까지의 데이터로만 계산하고, 체결은 다음 거래일 종가로 합니다. 마지막 거래일의 신호는 체결되지 않습니다.",
    "정수 주식 수만 체결하고, 남는 금액은 현금으로 둡니다(이자 없음).",
    "매수 비용 = 수량 × 체결가 × (1 + 수수료율), 매도 수령액 = 수량 × 체결가 × (1 − 수수료율 − 매도세율)입니다.",
    "수수료율과 매도세율의 기본값은 가정값이며 실제 수수료·세율과 다를 수 있습니다.",
    "여러 종목은 초기 자금을 똑같이 나눠 종목별로 따로 운용하고, 리밸런싱하지 않습니다.",
    "기간 끝에 매도하지 않고 마지막 종가로 평가합니다(매도 비용 미반영).",
    "호가 단위, 거래량 제약, 슬리피지, 배당은 반영하지 않습니다. 시세는 수정주가(FinanceDataReader) 기준입니다.",
    "Sharpe 유사 지표는 무위험 수익률을 0으로 가정합니다.",
]


class BacktestError(ValueError):
    """요청은 형식상 맞지만 데이터로 계산할 수 없다(예: 공통 거래일 부족). API는 400."""


class ParamError(ValueError):
    """전략 파라미터가 규칙에 맞지 않다. API는 422. field는 params 안의 키."""

    def __init__(self, field: str, message: str):
        super().__init__(message)
        self.field = field


@dataclass
class _Sleeve:
    ticker: str
    cash: float
    qty: int = 0
    fees: float = 0.0
    taxes: float = 0.0


def validate_params(strategy: str, params: dict | None, initial_cash: float) -> dict:
    """기본값을 채운 파라미터. 규칙에 어긋나면 ParamError."""
    params = dict(params or {})
    if strategy not in STRATEGIES:
        raise ParamError("strategy", f"strategy는 {', '.join(STRATEGIES)} 중 하나입니다.")
    allowed = {"buy_and_hold": set(), "sma_cross": {"short", "long"}, "dca": {"monthly_amount"}}[strategy]
    for k in params:
        if k not in allowed:
            raise ParamError(k, f"{strategy} 전략에는 '{k}' 파라미터가 없습니다.")
    for k, v in params.items():
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
            raise ParamError(k, f"'{k}'는 숫자여야 합니다.")
    if strategy == "sma_cross":
        out = {**SMA_DEFAULTS, **params}
        for k, (lo, hi) in (("short", SMA_SHORT_RANGE), ("long", SMA_LONG_RANGE)):
            if out[k] != int(out[k]) or not lo <= out[k] <= hi:
                raise ParamError(k, f"{k}는 {lo}~{hi} 사이의 정수입니다.")
            out[k] = int(out[k])
        if out["short"] >= out["long"]:
            raise ParamError("short", "short는 long보다 작아야 합니다.")
        return out
    if strategy == "dca":
        amount = params.get("monthly_amount", min(DCA_DEFAULT_AMOUNT, initial_cash))
        if not DCA_MIN_AMOUNT <= amount <= initial_cash:
            raise ParamError("monthly_amount", f"monthly_amount는 {DCA_MIN_AMOUNT:,}원 이상, 초기 자금 이하입니다.")
        return {"monthly_amount": float(amount)}
    return {}


# ---------- 신호(t일 종가까지만 본다) ----------


def sma(values: list[float], n: int) -> float | None:
    return sum(values[-n:]) / n if len(values) >= n else None


def sma_target(closes_so_far: list[float], short: int, long: int) -> bool | None:
    """t일까지의 종가만 받아 목표 보유 여부. 장기 SMA를 아직 못 구하면 None(판단 없음)."""
    s, l_ = sma(closes_so_far, short), sma(closes_so_far, long)
    if s is None or l_ is None:
        return None
    return s > l_


def signals(strategy: str, params: dict, days: list[date], closes: list[float]) -> list[str | None]:
    """거래일마다 'buy' / 'sell' / None. i번째 신호는 closes[:i+1]과 days[:i+1]만으로 정한다(룩어헤드 없음).

    sma_cross의 sell/buy는 "목표 상태"의 변화다. 실제 체결은 엔진이 보유 상태를 보고 다음 거래일에 한다.
    """
    out: list[str | None] = []
    for i in range(len(days)):
        if strategy == "buy_and_hold":
            out.append("buy" if i == 0 else None)
        elif strategy == "dca":
            out.append("buy" if i == 0 or days[i].month != days[i - 1].month or days[i].year != days[i - 1].year else None)
        else:
            target = sma_target(closes[: i + 1], params["short"], params["long"])
            out.append(None if target is None else ("buy" if target else "sell"))
    return out


# ---------- 엔진 ----------


def common_calendar(prices: dict[str, list[tuple[date, float]]], start: date, end: date) -> list[date]:
    sets = [{d for d, v in rows if start <= d <= end and v and v > 0} for rows in prices.values()]
    return sorted(set.intersection(*sets)) if sets else []


def _buy(sl: _Sleeve, budget: float, price: float, fee_rate: float) -> tuple[int, float, float]:
    qty = int(budget // (price * (1 + fee_rate))) if price > 0 else 0
    if qty <= 0:
        return 0, 0.0, 0.0
    gross = qty * price
    fee = gross * fee_rate
    sl.cash -= gross + fee
    sl.qty += qty
    sl.fees += fee
    return qty, gross, fee


def _sell(sl: _Sleeve, price: float, fee_rate: float, tax_rate: float) -> tuple[int, float, float, float]:
    qty = sl.qty
    gross = qty * price
    fee, tax = gross * fee_rate, gross * tax_rate
    sl.cash += gross - fee - tax
    sl.qty = 0
    sl.fees += fee
    sl.taxes += tax
    return qty, gross, fee, tax


def run_backtest(
    prices: dict[str, list[tuple[date, float]]],
    names: dict[str, str],
    strategy: str,
    params: dict | None,
    start: date,
    end: date,
    initial_cash: float,
    fee_rate: float = DEFAULT_FEE_RATE,
    sell_tax_rate: float = DEFAULT_SELL_TAX_RATE,
    index_series: list[tuple[date, float]] | None = None,
    max_points: int = MAX_POINTS,
    max_trades: int = MAX_TRADES,
    min_days: int = MIN_TRADING_DAYS,
) -> dict:
    """prices: ticker → 날짜 오름차순 (날짜, 종가). 공통 거래일이 min_days(기본 30) 미만이면 BacktestError."""
    tickers = list(prices)
    if not 1 <= len(tickers) <= MAX_TICKERS:
        raise ParamError("tickers", f"종목은 1~{MAX_TICKERS}개입니다.")
    if not 0 <= fee_rate <= MAX_RATE or not 0 <= sell_tax_rate <= MAX_RATE:
        raise ParamError("fee_rate", f"수수료율과 매도세율은 0~{MAX_RATE} 사이입니다.")
    if start > end:
        raise ParamError("start", "start는 end보다 늦을 수 없습니다.")
    params = validate_params(strategy, params, initial_cash)

    days = common_calendar(prices, start, end)
    if len(days) < min_days:
        have = ", ".join(
            f"{t} {rows[0][0].isoformat()}~{rows[-1][0].isoformat()}" if rows else f"{t} 시세 없음" for t, rows in prices.items()
        )
        raise BacktestError(
            f"요청 기간에 모든 종목의 종가가 있는 거래일이 {len(days)}일이라 백테스트할 수 없습니다"
            f"(최소 {min_days}일). 보유 시세: {have}."
        )

    close = {t: dict(rows) for t, rows in prices.items()}
    series = {t: [close[t][d] for d in days] for t in tickers}
    sig = {t: signals(strategy, params, days, series[t]) for t in tickers}

    n = len(tickers)
    sleeves = {t: _Sleeve(t, initial_cash / n) for t in tickers}
    per_month = params.get("monthly_amount", 0.0) / n
    trades, skipped_dca = [], 0
    curve = []
    for i, d in enumerate(days):
        if i > 0:  # 어제(i−1) 신호를 오늘(i) 종가로 체결
            for t in tickers:
                s, sl, price = sig[t][i - 1], sleeves[t], series[t][i]
                if s is None:
                    continue
                if strategy == "sma_cross" and s == "sell" and sl.qty > 0:
                    q, gross, fee, tax = _sell(sl, price, fee_rate, sell_tax_rate)
                    trades.append(_trade(t, "sell", days[i - 1], d, q, price, gross, fee, tax, sl.cash))
                elif s == "buy" and (strategy != "sma_cross" or sl.qty == 0):
                    budget = min(per_month, sl.cash) if strategy == "dca" else sl.cash
                    q, gross, fee = _buy(sl, budget, price, fee_rate)
                    if q:
                        trades.append(_trade(t, "buy", days[i - 1], d, q, price, gross, fee, 0.0, sl.cash))
                    elif strategy == "dca":
                        skipped_dca += 1
        total = sum(sl.cash + sl.qty * series[t][i] for t, sl in sleeves.items())
        curve.append({"date": d.isoformat(), "total_value": round(total, 2),
                      "cash": round(sum(sl.cash for sl in sleeves.values()), 2)})

    values = [p["total_value"] for p in curve]
    rets = [r for r in (pct(b, a) for a, b in zip(values, values[1:])) if r is not None]
    vol_d = statistics.stdev(rets) if len(rets) >= 2 else None
    mean_d = statistics.fmean(rets) if rets else None
    final = values[-1]
    span_days = (days[-1] - days[0]).days
    total_ret = pct(final, initial_cash)
    cagr = ((final / initial_cash) ** (365.25 / span_days) - 1) * 100 if span_days > 0 and final > 0 else None
    mdd = max_drawdown(days, values)

    notices = []
    adjusted = days[0] != start or days[-1] != end
    if adjusted:
        notices.append(f"요청 기간({start.isoformat()}~{end.isoformat()}) 중 모든 종목의 종가가 있는 구간"
                       f"({days[0].isoformat()}~{days[-1].isoformat()})으로 계산했습니다.")
    if skipped_dca:
        notices.append(f"현금이 1주 가격보다 적어 적립 매수를 건너뛴 경우가 {skipped_dca}번 있습니다.")
    if not trades:
        notices.append("기간 중 체결된 거래가 없습니다(신호가 없었거나 1주도 살 수 없었습니다).")
    union = set().union(*({d for d, _ in rows if days[0] <= d <= days[-1]} for rows in prices.values()))
    dropped = len(union) - len(days)
    if dropped:
        notices.append(f"일부 종목의 종가가 없는 {dropped}거래일은 공통 거래일에서 뺐습니다.")

    benchmark, bench_notice = _benchmark(index_series or [], days[0], days[-1], total_ret)
    if bench_notice:
        notices.append(bench_notice)

    sampled = sample_points(curve, max_points, key="total_value",
                            keep=[i for i, d in enumerate(days) if d.isoformat() in (mdd["peak_date"], mdd["trough_date"])])
    return {
        "input": {
            "tickers": [{"ticker": t, "name": names.get(t, t)} for t in tickers],
            "strategy": strategy, "params": params, "start": start.isoformat(), "end": end.isoformat(),
            "initial_cash": float(initial_cash), "fee_rate": fee_rate, "sell_tax_rate": sell_tax_rate,
        },
        "period": {"requested_start": start.isoformat(), "requested_end": end.isoformat(),
                   "start": days[0].isoformat(), "end": days[-1].isoformat(), "trading_days": len(days),
                   "adjusted": adjusted},
        "metrics": {
            "final_value": round(final, 2),
            "total_return_pct": _r(total_ret),
            "cagr_pct": _r(cagr),
            "max_drawdown": mdd,
            "annualized_volatility_pct": _r(vol_d * math.sqrt(TRADING_DAYS_PER_YEAR) if vol_d is not None else None),
            "sharpe_like": _r(mean_d / vol_d * math.sqrt(TRADING_DAYS_PER_YEAR) if vol_d else None, 3),
            "trade_count": len(trades),
            "buy_count": sum(1 for x in trades if x["side"] == "buy"),
            "sell_count": sum(1 for x in trades if x["side"] == "sell"),
            "fees_paid": round(sum(sl.fees for sl in sleeves.values()), 2),
            "taxes_paid": round(sum(sl.taxes for sl in sleeves.values()), 2),
            "ending_cash": curve[-1]["cash"],
        },
        "per_ticker": [
            {"ticker": t, "name": names.get(t, t), "start_value": round(initial_cash / n, 2),
             "final_value": round(sl.cash + sl.qty * series[t][-1], 2),
             "return_pct": _r(pct(sl.cash + sl.qty * series[t][-1], initial_cash / n)),
             "quantity": sl.qty, "trade_count": sum(1 for x in trades if x["ticker"] == t)}
            for t, sl in sleeves.items()
        ],
        "benchmark": benchmark,
        "curve": {"points": sampled, "total_points": len(curve), "sampled": len(sampled) < len(curve), "max_points": max_points},
        "trades": {"items": trades[:max_trades], "total": len(trades), "truncated": len(trades) > max_trades,
                   "max_items": max_trades},
        "assumptions": list(ASSUMPTIONS),
        "data_warnings": data_warnings(prices, days[0], days[-1], names),
        "notices": notices,
    }


def _r(v, n: int = 2):
    return None if v is None else round(v, n)


def _trade(ticker, side, signal_day, day, qty, price, gross, fee, tax, cash_after) -> dict:
    return {"ticker": ticker, "side": side, "signal_date": signal_day.isoformat(), "date": day.isoformat(),
            "quantity": qty, "price": price, "amount": round(gross, 2), "fee": round(fee, 2), "tax": round(tax, 2),
            "cash_after": round(cash_after, 2)}


def _benchmark(index_series, start: date, end: date, total_ret) -> tuple[dict | None, str | None]:
    """같은 기간 코스피 매수 후 보유(첫날 종가 → 마지막 날 종가, 비용 없음) 대비."""
    rows = [(d, v) for d, v in index_series if v and v > 0]
    if not rows:
        return None, f"{BENCHMARK_NAME} 지수 데이터가 없어 벤치마크 비교를 하지 않았습니다."
    first_on_or_after = next(((d, v) for d, v in rows if d >= start), None)
    last_on_or_before = next(((d, v) for d, v in reversed(rows) if d <= end), None)
    if first_on_or_after is None or last_on_or_before is None or first_on_or_after[0] > last_on_or_before[0]:
        return None, f"{BENCHMARK_NAME} 지수 데이터가 이 기간과 겹치지 않아 벤치마크 비교를 하지 않았습니다."
    (d0, v0), (d1, v1) = first_on_or_after, last_on_or_before
    ret = pct(v1, v0)
    full = d0 == start and d1 == end
    out = {"index_code": BENCHMARK_CODE, "index_name": BENCHMARK_NAME, "start_date": d0.isoformat(), "end_date": d1.isoformat(),
           "return_pct": _r(ret), "excess_return_pct": _r(total_ret - ret) if total_ret is not None and ret is not None else None,
           "covers_full_period": full}
    note = None if full else (f"{BENCHMARK_NAME} 지수 데이터가 {d0.isoformat()}~{d1.isoformat()}만 있어 그 구간의 지수 수익률과 비교했습니다"
                              "(전략 수익률은 전체 기간).")
    return out, note


def data_warnings(prices, start: date, end: date, names: dict[str, str]) -> list[dict]:
    """쓰인 구간의 가격 불연속(±30% 초과 일간 변동)·0 이하 값·같은 종가가 이어지는 구간."""
    out = []
    for t, rows in prices.items():
        window = [(d, v) for d, v in rows if start <= d <= end]
        for issue in check_series(window):
            if issue.kind in ("jump", "non_positive", "flat_run"):
                out.append({"ticker": t, "name": names.get(t, t), **issue.as_dict()})
    return out
