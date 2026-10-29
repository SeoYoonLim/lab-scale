"""모의투자 성과 추적: 체결 내역을 일자순으로 재생해 거래일 종가 기준 자산 곡선과 성과 지표를 계산한다(DB 없음, LLM 없음).

입력은 평범한 값이다(체결 목록, 종목별 일별 종가, 거래일 달력, 지수 종가). DB 조회는 app/performance_db.py가 한다.
모든 문구는 관찰 사실만 담는다(투자 권유 없음). 금지 표현 검사는 tests/test_performance.py가 한다.

규칙
- 자산 = 현금 + Σ(보유 수량 × 그 거래일 종가). 체결은 체결일(KST)의 장 마감 종가로 평가한다(같은 날 여러 건은 체결 순서대로).
  주말·휴일에 체결된 주문은 다음 거래일 종가에 반영된다. 매수·매도 계산은 주문과 같은 app.portfolio.apply_buy/apply_sell이다.
- 거래일 = 달력(시세가 한 종목이라도 있는 날). 그날 종가가 없는 보유 종목은 직전 종가(없으면 마지막 체결가)를 쓰고 data_gaps에 남긴다.
- data_date(마지막 거래일) 뒤에 체결된 주문은 아직 종가가 없어 곡선·지표에 넣지 않고 pending_count로만 센다.
- 기간 시작값: all은 초기 자금(INITIAL_BALANCE), 1m/3m/6m은 data_date에서 그 개월 수를 뺀 날 이전 마지막 거래일의 자산.
  첫 체결 전 구간의 자산은 초기 자금 그대로다.
- 종목별 손익(기여도) = 기간 끝 평가액 + 기간 중 매도대금 − 기간 중 매수대금 − 기간 시작 평가액. 종목 합은 기간 손익과 같다.
"""

import bisect
import math
import statistics
from dataclasses import dataclass, field
from datetime import date

from app.portfolio import InsufficientBalance, InsufficientQuantity, apply_buy, apply_sell

PERIOD_MONTHS = {"1m": 1, "3m": 3, "6m": 6, "all": None}
MAX_POINTS = 400  # 자산 곡선 응답 포인트 상한(넘으면 간격 샘플링)
TRADING_DAYS_PER_YEAR = 252
BENCHMARK_CODE = "KS11"
BENCHMARK_NAME = "코스피"

MSG_NO_TRADES = "아직 체결된 모의투자 주문이 없습니다. 주문이 체결되면 거래일 종가 기준 자산 곡선과 성과 지표가 계산됩니다."
MSG_ONLY_PENDING = "체결된 주문이 아직 종가에 반영되지 않았습니다. 다음 거래일 종가가 수집되면 자산 곡선에 포함됩니다."
MSG_NO_PRICES = "수집된 시세가 없어 자산 곡선을 계산할 수 없습니다."


@dataclass(frozen=True)
class TradeIn:
    day: date  # 체결일(KST)
    ticker: str
    name: str
    side: str  # "buy" | "sell"
    quantity: int
    price: float


@dataclass
class _Position:
    quantity: int = 0
    avg_price: float = 0.0
    last_trade_price: float = 0.0


@dataclass
class _Flow:
    """한 종목의 기간 중 현금 흐름과 실현손익."""

    buys: float = 0.0
    sells: float = 0.0
    realized: float = 0.0
    start_value: float = 0.0


@dataclass
class _Gap:
    kind: str  # previous_close | trade_price
    days: list = field(default_factory=list)


def months_before(d: date, months: int) -> date:
    y, m = divmod(d.year * 12 + d.month - 1 - months, 12)
    m += 1
    last = [31, 29 if y % 4 == 0 and (y % 100 or y % 400 == 0) else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][m - 1]
    return date(y, m, min(d.day, last))


def pct(new: float, old: float) -> float | None:
    """(new / old − 1) × 100. old가 0 이하면 None(0으로 나누지 않는다)."""
    return None if not old or old <= 0 else (new / old - 1) * 100


def _r(v: float | None, n: int = 2) -> float | None:
    return None if v is None else round(v, n)


class _Prices:
    """종목별 종가 조회: 그날 종가, 없으면 직전 종가."""

    def __init__(self, closes: dict[str, list[tuple[date, float]]]):
        self.dates = {t: [d for d, _ in rows] for t, rows in closes.items()}
        self.values = {t: [v for _, v in rows] for t, rows in closes.items()}

    def at(self, ticker: str, day: date) -> tuple[float | None, date | None]:
        ds = self.dates.get(ticker, [])
        i = bisect.bisect_right(ds, day) - 1
        return (self.values[ticker][i], ds[i]) if i >= 0 else (None, None)


def sample_points(points: list[dict], max_points: int = MAX_POINTS, key: str = "total_asset", keep=()) -> list[dict]:
    """max_points 이하로 고르게 줄인다. 첫 점·마지막 점·최고점·최저점과 keep(인덱스)은 반드시 남긴다."""
    n = len(points)
    if n <= max_points:
        return list(points)
    values = [p[key] for p in points]
    must = {0, n - 1, values.index(max(values)), values.index(min(values)), *keep}
    room = max_points - len(must)
    picked = set(must)
    if room > 0:
        step = (n - 1) / (room + 1)
        picked.update(round(step * (k + 1)) for k in range(room))
    # |must| + room = max_points라 넘치지 않는다. 반올림이 must와 겹쳐 조금 모자랄 수는 있다(상한만 지키면 된다).
    return [points[i] for i in sorted(picked)]


def max_drawdown(days: list[date], values: list[float]) -> dict:
    """고점 대비 최대 하락률(≤ 0, %)과 그 고점·저점·회복일. 하락이 없으면 0과 날짜 null."""
    best = {"pct": 0.0, "peak_date": None, "trough_date": None, "recovery_date": None, "duration_days": 0}
    if not values:
        return best
    peak_i, worst, worst_peak_i, worst_trough_i = 0, 0.0, None, None
    for i, v in enumerate(values):
        if v > values[peak_i]:
            peak_i = i
        dd = pct(v, values[peak_i])
        if dd is not None and dd < worst:
            worst, worst_peak_i, worst_trough_i = dd, peak_i, i
    if worst_trough_i is None:
        return best
    peak_value = values[worst_peak_i]
    recovery = next((days[j] for j in range(worst_trough_i + 1, len(values)) if values[j] >= peak_value), None)
    return {
        "pct": round(worst, 2),
        "peak_date": days[worst_peak_i].isoformat(),
        "trough_date": days[worst_trough_i].isoformat(),
        "recovery_date": recovery.isoformat() if recovery else None,
        "duration_days": worst_trough_i - worst_peak_i,  # 고점에서 저점까지 거래일 수
    }


def daily_returns(values: list[float]) -> list[float]:
    """연속한 두 거래일 자산의 등락률(%). 앞 값이 0 이하인 구간은 건너뛴다."""
    out = []
    for prev, cur in zip(values, values[1:]):
        r = pct(cur, prev)
        if r is not None:
            out.append(r)
    return out


def volatility(returns: list[float]) -> float | None:
    """일간 수익률의 표본 표준편차(%). 수익률이 2개 미만이면 None."""
    return statistics.stdev(returns) if len(returns) >= 2 else None


def _benchmark(index_series, curve_days, curve_values, start_day, start_value, end_day) -> tuple[dict | None, str | None]:
    if not index_series:
        return None, f"{BENCHMARK_NAME} 지수 데이터가 없어 벤치마크 비교를 하지 않았습니다."
    idx_dates = [d for d, _ in index_series]
    lo, hi = max(start_day, idx_dates[0]), min(end_day, idx_dates[-1])
    if lo > hi:
        return None, f"{BENCHMARK_NAME} 지수 데이터가 이 기간과 겹치지 않아 벤치마크 비교를 하지 않았습니다."

    def idx_at(d):
        return index_series[bisect.bisect_right(idx_dates, d) - 1][1]

    def port_at(d):
        if d == start_day:
            return start_value
        return curve_values[bisect.bisect_right(curve_days, d) - 1]

    i_ret, p_ret = pct(idx_at(hi), idx_at(lo)), pct(port_at(hi), port_at(lo))
    full = lo == start_day and hi == end_day
    out = {
        "index_code": BENCHMARK_CODE,
        "index_name": BENCHMARK_NAME,
        "start_date": lo.isoformat(),
        "end_date": hi.isoformat(),
        "index_return_pct": _r(i_ret),
        "portfolio_return_pct": _r(p_ret),
        "excess_return_pct": _r(p_ret - i_ret) if p_ret is not None and i_ret is not None else None,
        "covers_full_period": full,
    }
    notice = None if full else (
        f"{BENCHMARK_NAME} 지수 데이터가 {lo.isoformat()}~{hi.isoformat()}만 있어 그 구간끼리만 비교했습니다."
    )
    return out, notice


def _empty(period, data_date, initial, message, pending, notices):
    point = [{"date": data_date.isoformat(), "total_asset": float(initial), "cash_balance": float(initial),
              "eval_amount": 0.0, "return_pct": 0.0}] if data_date else []
    return {
        "period": period,
        "start_date": data_date.isoformat() if data_date else None,
        "end_date": data_date.isoformat() if data_date else None,
        "initial_balance": float(initial),
        "message": message,
        "summary": {"start_value": float(initial), "end_value": float(initial), "cash_balance": float(initial),
                    "eval_amount": 0.0, "profit_loss": 0.0, "cumulative_return_pct": 0.0, "period_return_pct": 0.0,
                    "cash_weight_pct": 100.0},
        "risk": {"max_drawdown": max_drawdown([], []), "daily_volatility_pct": None, "annualized_volatility_pct": None,
                 "daily_return_count": 0},
        "trades": {"count": 0, "buys": 0, "sells": 0, "wins": 0, "losses": 0, "even": 0, "win_rate_pct": None,
                   "realized_profit_loss": 0.0, "pending_count": pending},
        "contributions": [],
        "benchmark": None,
        "curve": {"points": point, "total_points": len(point), "sampled": False, "max_points": MAX_POINTS},
        "data_gaps": [],
        "notices": notices,
    }


def compute_performance(
    trades: list[TradeIn],
    closes: dict[str, list[tuple[date, float]]],
    calendar: list[date],
    index_series: list[tuple[date, float]],
    period: str,
    initial_balance: float,
    max_points: int = MAX_POINTS,
) -> dict:
    """trades는 체결 순서, closes·calendar·index_series는 날짜 오름차순이고 장중 행이 이미 빠져 있어야 한다."""
    if period not in PERIOD_MONTHS:
        raise ValueError("period")
    calendar = sorted(set(calendar))
    data_date = calendar[-1] if calendar else None
    if data_date is None:
        return _empty(period, None, initial_balance, MSG_NO_PRICES if trades else MSG_NO_TRADES, len(trades), [])

    done = [t for t in trades if t.day <= data_date]
    pending = len(trades) - len(done)
    notices = []
    if pending:
        notices.append(f"마지막 종가일({data_date.isoformat()}) 이후 체결된 주문 {pending}건은 아직 종가가 없어 반영하지 않았습니다.")
    if not done:
        return _empty(period, data_date, initial_balance, MSG_ONLY_PENDING if pending else MSG_NO_TRADES, pending, notices)

    first_day = done[0].day
    before_first = [d for d in calendar if d < first_day]
    inception = before_first[-1] if before_first else next(d for d in calendar if d >= first_day)
    if period == "all":
        start_day = inception
    else:
        target = months_before(data_date, PERIOD_MONTHS[period])
        on_or_before = [d for d in calendar if d <= target]
        start_day = on_or_before[-1] if on_or_before else calendar[0]
    replay_from = min(start_day, inception)
    days = [d for d in calendar if replay_from <= d <= data_date]

    prices = _Prices(closes)
    cash = float(initial_balance)
    pos: dict[str, _Position] = {}
    names = {t.ticker: t.name for t in done}
    flows: dict[str, _Flow] = {}
    gaps: dict[tuple[str, str], _Gap] = {}
    sells = []  # 기간 안 매도의 실현손익
    count = buys = 0
    skipped = 0
    # all의 시작값은 첫 체결 전 초기 자금이다. 첫 체결 전 거래일이 없으면(체결이 첫 거래일보다 앞서면) 시작점에도 체결이 반영된다.
    in_window_from_start = period == "all" and not before_first

    curve_days, curve_values, points = [], [], []
    ti = 0
    for d in days:
        in_window = d > start_day or in_window_from_start
        while ti < len(done) and done[ti].day <= d:
            t = done[ti]
            ti += 1
            p = pos.setdefault(t.ticker, _Position())
            try:
                if t.side == "buy":
                    res = apply_buy(cash, t.quantity, t.price, p.quantity, p.avg_price)
                    p.avg_price = res["avg_price"]
                else:
                    avg_before = p.avg_price
                    res = apply_sell(cash, t.quantity, t.price, p.quantity)
            except (InsufficientBalance, InsufficientQuantity):
                skipped += 1
                continue
            amount = round(t.price * t.quantity, 2)
            cash, p.quantity, p.last_trade_price = res["cash_balance"], res["quantity"], t.price
            if in_window:
                f = flows.setdefault(t.ticker, _Flow())
                count += 1
                if t.side == "buy":
                    buys += 1
                    f.buys += amount
                else:
                    realized = round((t.price - avg_before) * t.quantity, 2)
                    f.sells += amount
                    f.realized += realized
                    sells.append(realized)
            if p.quantity == 0:
                p.avg_price = 0.0  # 전량 매도하면 다음 매수는 새 평균단가로 시작(보유 행 삭제와 같은 효과)

        held_value = 0.0
        for ticker, p in pos.items():
            if p.quantity == 0:
                continue
            close, close_day = prices.at(ticker, d)
            if close is None:
                close, kind = p.last_trade_price, "trade_price"
            else:
                kind = None if close_day == d else "previous_close"
            if kind and d >= start_day:
                gaps.setdefault((ticker, kind), _Gap(kind)).days.append(d)
            held_value += p.quantity * close
        total = round(cash + held_value, 2)
        if d == start_day and not in_window_from_start:
            # 기간 시작 평가액(기여도 계산의 시작값). all은 첫 체결 전 거래일이라 보유가 없다.
            for ticker, p in pos.items():
                if p.quantity:
                    close, _ = prices.at(ticker, d)
                    flows.setdefault(ticker, _Flow()).start_value = p.quantity * (close if close is not None else p.last_trade_price)
        if d >= start_day:
            curve_days.append(d)
            curve_values.append(total)
            points.append({"date": d.isoformat(), "total_asset": total, "cash_balance": round(cash, 2),
                           "eval_amount": round(held_value, 2)})

    start_value = float(initial_balance) if period == "all" else curve_values[0]
    end_value = curve_values[-1]
    for p in points:
        p["return_pct"] = _r(pct(p["total_asset"], start_value))

    # 기간 끝 종목별 평가와 손익
    contributions = []
    end_eval = 0.0
    for ticker in sorted(set(flows) | {t for t, p in pos.items() if p.quantity}):
        p = pos.get(ticker, _Position())
        f = flows.get(ticker, _Flow())
        close, _ = prices.at(ticker, data_date)
        price = close if close is not None else p.last_trade_price
        value = p.quantity * price
        end_eval += value
        pnl = value + f.sells - f.buys - f.start_value
        contributions.append({
            "ticker": ticker,
            "company_name": names.get(ticker, ticker),
            "profit_loss": round(pnl, 2),
            "contribution_pct": _r(pnl / start_value * 100 if start_value > 0 else None),
            "quantity": p.quantity,
            "eval_amount": round(value, 2),
            "unrealized_profit_loss": round((price - p.avg_price) * p.quantity, 2) if p.quantity else None,
            "realized_profit_loss": round(f.realized, 2),
        })
    contributions.sort(key=lambda c: (-c["profit_loss"], c["ticker"]))

    returns = daily_returns(curve_values)
    vol = volatility(returns)
    mdd = max_drawdown(curve_days, curve_values)
    benchmark, bench_notice = _benchmark(index_series, curve_days, curve_values, start_day, start_value, data_date)
    if bench_notice:
        notices.append(bench_notice)
    if skipped:
        notices.append(f"잔고·수량이 맞지 않아 재생하지 못한 체결 {skipped}건은 계산에서 뺐습니다.")
    if period != "all" and start_day < inception:
        notices.append(f"첫 체결일({first_day.isoformat()}) 전 구간은 초기 자금 그대로입니다.")

    wins = sum(1 for x in sells if x > 0)
    losses = sum(1 for x in sells if x < 0)
    sampled = sample_points(points, max_points, keep=[i for i, d in enumerate(curve_days)
                                                      if d.isoformat() in (mdd["peak_date"], mdd["trough_date"])])
    data_gaps = [
        {"ticker": t, "company_name": names.get(t, t), "kind": g.kind, "days": len(g.days),
         "first_date": g.days[0].isoformat(), "last_date": g.days[-1].isoformat()}
        for (t, _), g in sorted(gaps.items())
    ]
    if data_gaps:
        notices.append("종가가 없는 날이 있는 종목은 직전 종가(없으면 마지막 체결가)로 평가했습니다(data_gaps).")

    return {
        "period": period,
        "start_date": curve_days[0].isoformat(),
        "end_date": data_date.isoformat(),
        "initial_balance": float(initial_balance),
        "message": None,
        "summary": {
            "start_value": round(start_value, 2),
            "end_value": end_value,
            "cash_balance": points[-1]["cash_balance"],
            "eval_amount": round(end_eval, 2),
            "profit_loss": round(end_value - start_value, 2),
            "cumulative_return_pct": _r(pct(end_value, initial_balance)),
            "period_return_pct": _r(pct(end_value, start_value)),
            "cash_weight_pct": _r(points[-1]["cash_balance"] / end_value * 100 if end_value > 0 else None),
        },
        "risk": {
            "max_drawdown": mdd,
            "daily_volatility_pct": _r(vol, 4),
            "annualized_volatility_pct": _r(vol * math.sqrt(TRADING_DAYS_PER_YEAR), 4) if vol is not None else None,
            "daily_return_count": len(returns),
        },
        "trades": {
            "count": count, "buys": buys, "sells": len(sells), "wins": wins, "losses": losses,
            "even": len(sells) - wins - losses,
            "win_rate_pct": _r(wins / len(sells) * 100) if sells else None,
            "realized_profit_loss": round(sum(sells), 2),
            "pending_count": pending,
        },
        "contributions": contributions,
        "benchmark": benchmark,
        "curve": {"points": sampled, "total_points": len(points), "sampled": len(sampled) < len(points),
                  "max_points": max_points},
        "data_gaps": data_gaps,
        "notices": notices,
    }
