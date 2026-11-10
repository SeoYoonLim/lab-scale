import type { Locale } from './contracts/common'
import type {
  BenchmarkCode,
  BenchmarkComparison,
  BenchmarkPoint,
  MaxDrawdown,
  PerformanceMetrics,
  PerformancePeriod,
  PerformancePoint,
  PerformanceStatus,
  PerformanceView,
  PerformanceWarning,
} from './contracts/performance'
import type { PerformanceApi } from './domains/performance'
import { currentUserId, storeFor } from './mock'
import {
  collectedSeries,
  delay,
  expectedTradingDate,
  kstDate,
  getPriceSeries,
  mockDataLagDays,
  syntheticIndexCloses,
} from './mock-core'
import { toLocale, translate } from '../i18n'

// 포트폴리오 성과·벤치마크 비교 mock (docs/FRONTEND_HANDOFF_PORTFOLIO_PERFORMANCE.md 2절).
// 백엔드와 같은 방식으로 체결 내역을 날짜순으로 다시 재생해서 곡선을 만든다(현재 보유를 과거에 붙이지 않는다):
//   총자산 = 현금 + sum(그날 보유 수량 x 그날 종가), 현금은 체결가 x 수량으로만 바뀐다(수수료·세금·입출금 없음).
//   지수(코스피·코스닥)는 DB 값이 아니라 시드 기반 가짜 시계열이다.
const INITIAL_BALANCE = 10_000_000
const PERIOD_MONTHS: Record<Exclude<PerformancePeriod, 'all'>, number> = { '1m': 1, '3m': 3, '6m': 6, '1y': 12 }
const BENCHMARKS: Record<BenchmarkCode, { name: Record<'ko' | 'en', string>; base: number; seed: number }> = {
  KS11: { name: { ko: '코스피', en: 'KOSPI' }, base: 2700, seed: 11 },
  KQ11: { name: { ko: '코스닥', en: 'KOSDAQ' }, base: 850, seed: 22 },
}

const round2 = (n: number) => Math.round(n * 100) / 100
const round4 = (n: number) => Math.round(n * 10000) / 10000

function pickText(locale: Locale, ko: string, en: string): string {
  return locale === 'ko-KR' ? ko : en
}

// 거래일 달력(= 수집된 시세의 날짜). 지연 훅이 걸려 있으면 마지막 N일이 빠진다.
function calendar(): string[] {
  return collectedSeries('005930').map((p) => p.price_date)
}

function benchmarkCloses(code: BenchmarkCode, dates: string[]): number[] {
  // 같은 날짜는 항상 같은 값이 나오게 지연 훅과 무관한 전체 길이로 만든 뒤 필요한 앞부분만 자른다.
  const all = syntheticIndexCloses(BENCHMARKS[code].base, BENCHMARKS[code].seed, getPriceSeries('005930').length)
  return dates.map((_, i) => all[i])
}

function addMonths(dateStr: string, months: number): string {
  const d = new Date(`${dateStr}T00:00:00Z`)
  d.setUTCMonth(d.getUTCMonth() - months)
  return d.toISOString().slice(0, 10)
}

function maxDrawdown(points: { date: string; value: number }[]): MaxDrawdown | null {
  if (points.length < 2) return null
  let peakIdx = 0
  let worst = 0
  let worstPeak = 0
  let worstTrough = 0
  for (let i = 0; i < points.length; i += 1) {
    if (points[i].value > points[peakIdx].value) peakIdx = i
    const dd = points[i].value / points[peakIdx].value - 1
    if (dd < worst) {
      worst = dd
      worstPeak = peakIdx
      worstTrough = i
    }
  }
  const recovery = points.slice(worstTrough + 1).find((p) => p.value >= points[worstPeak].value)
  return {
    pct: round2(worst * 100),
    peak_date: worst < 0 ? points[worstPeak].date : null,
    trough_date: worst < 0 ? points[worstTrough].date : null,
    recovery_date: worst < 0 ? (recovery?.date ?? null) : null,
    duration_days: worstTrough - worstPeak,
  }
}

function emptyMetrics(): PerformanceMetrics {
  return {
    highest: null,
    lowest: null,
    max_drawdown: null,
    daily_volatility_pct: null,
    annualized_volatility_pct: null,
    annualized_return_pct: null,
    daily_return_count: 0,
    trading_days: 0,
    sample_rules: { volatility_min_returns: 20, annualized_return_min_returns: 252 },
  }
}

async function getPerformance(params: { period?: PerformancePeriod; benchmark?: BenchmarkCode; locale?: string } = {}): Promise<PerformanceView> {
  await delay(300)
  const store = storeFor(currentUserId())
  const locale = toLocale(params.locale ?? store.preferences.locale)
  const period = params.period ?? 'all'
  const benchmark = params.benchmark ?? 'KS11'
  const benchName = BENCHMARKS[benchmark].name[locale === 'ko-KR' ? 'ko' : 'en']

  const dates = calendar()
  const dataDate = dates.at(-1)!
  const expectedDate = expectedTradingDate()
  const lag = mockDataLagDays()
  const disclaimer = pickText(
    locale,
    '거래일 종가 기준의 가격 수익률입니다. 배당·분배금은 포함하지 않고, 분할·병합 등 기업행사를 따로 보정하지 않습니다. 외부 입출금 기능이 없어 단순 수익률이며 시간가중수익률과 같습니다. 과거 성과는 미래 성과를 보장하지 않으며 매수·매도 권유가 아닙니다. ',
    'Price return based on closing prices on trading days. Dividends and distributions are not included, and corporate actions such as splits are not adjusted. There are no deposits or withdrawals, so the simple return equals the time-weighted return. Past performance does not guarantee future results and is not a recommendation to buy or sell. ',
  ) + translate(locale, 'system.disclaimer')

  const warnings: PerformanceWarning[] = []
  if (lag > 0) {
    warnings.push({
      kind: 'data_delayed',
      date: dataDate,
      expected: expectedDate,
      lag,
      message: pickText(
        locale,
        `마지막 종가는 ${dataDate}이며 기대 거래일(${expectedDate})보다 ${lag}영업일 지연됐습니다. 오늘 시장 상황이 아닙니다.`,
        `The latest closing price is from ${dataDate}, ${lag} business days behind the expected trading day (${expectedDate}). This is not today's market.`,
      ),
    })
  }

  const base = {
    generated_at: new Date().toISOString(),
    calc_version: 'performance.v2',
    locale,
    history_verified: true,
    period,
    data_date: dataDate,
    expected_date: expectedDate,
    data_lag_business_days: lag,
    initial_balance: INITIAL_BALANCE,
    benchmark_code: benchmark,
    benchmark_name: benchName,
    estimated_dates: [] as string[],
    cash_flows: { supported: false, external_flow_count: 0, return_method: 'simple' },
    price_basis: { source: 'stock_price.close_price', dividends_included: false, corporate_actions_adjusted: 'not_verified' },
    disclaimer,
  }

  function unavailable(status: PerformanceStatus, message: string): PerformanceView {
    return {
      ...base,
      status,
      message,
      requested_start_date: null,
      start_date: null,
      end_date: dataDate,
      summary: {
        current_cash: store.cashBalance,
        start_value: null,
        end_value: null,
        profit_loss: null,
        period_return_pct: null,
        cumulative_return_pct: null,
      },
      metrics: emptyMetrics(),
      series: { points: [], total_points: 0, sampled: false, max_points: 400 },
      comparison: {
        status: 'portfolio_unavailable',
        points: [],
        benchmark_name: benchName,
        message: pickText(locale, '포트폴리오 성과를 계산하지 못해 지수와 비교하지 않았습니다.', 'Portfolio performance could not be calculated, so it was not compared with the index.'),
      },
      warnings,
    }
  }

  if (store.trades.length === 0) {
    return unavailable(
      'no_trades',
      pickText(locale, '체결된 주문이 없어 성과를 계산할 수 없습니다. 주문이 체결되면 거래일 종가 기준으로 계산합니다.', 'There are no filled orders, so performance cannot be calculated. It is calculated from closing prices once an order is filled.'),
    )
  }

  // 체결 -> 반영되는 거래일 인덱스(주말·휴일 체결은 다음 거래일 종가에 반영, 마지막 종가일 뒤 체결은 아직 미반영)
  const trades = store.trades.slice().sort((a, b) => a.executed_at.localeCompare(b.executed_at) || a.id - b.id)
  const effective = trades.map((t) => {
    const tradeDate = kstDate(t.executed_at)
    const idx = dates.findIndex((d) => d >= tradeDate)
    return { trade: t, idx: idx === -1 ? null : idx }
  })
  const applied = effective.filter((e): e is { trade: (typeof trades)[number]; idx: number } => e.idx != null)
  const pendingCount = effective.length - applied.length
  if (pendingCount > 0) {
    warnings.push({
      kind: 'pending_trades',
      count: pendingCount,
      message: pickText(
        locale,
        `마지막 종가일(${dataDate}) 뒤에 체결된 주문 ${pendingCount}건은 아직 반영하지 않았습니다.`,
        `${pendingCount} order(s) filled after the last closing date (${dataDate}) are not included yet.`,
      ),
    })
  }
  if (applied.length === 0) {
    return unavailable(
      'pending_only',
      pickText(locale, '체결된 주문이 아직 종가에 반영되지 않았습니다. 다음 거래일 종가가 수집되면 계산합니다.', 'Filled orders are not reflected in a closing price yet. They are calculated once the next trading day\'s close is collected.'),
    )
  }

  // 요청 기간의 시작 인덱스와 실제 시작(첫 체결 직전 거래일)
  const firstIdx = applied[0].idx
  const inceptionIdx = Math.max(0, firstIdx - 1)
  const requestedStartDate = period === 'all' ? null : addMonths(dataDate, PERIOD_MONTHS[period])
  const requestedIdx = requestedStartDate == null ? 0 : Math.max(0, dates.findLastIndex((d) => d <= requestedStartDate))
  const startIdx = Math.max(requestedIdx, inceptionIdx)
  if (requestedStartDate != null && requestedIdx < inceptionIdx) {
    warnings.push({
      kind: 'period_before_inception',
      requested: requestedStartDate,
      start: dates[inceptionIdx],
      message: pickText(
        locale,
        `요청한 기간 시작(${requestedStartDate})이 첫 체결 전이라 ${dates[inceptionIdx]}부터 계산했습니다.`,
        `The requested start (${requestedStartDate}) is before the first fill, so it was calculated from ${dates[inceptionIdx]}.`,
      ),
    })
  }

  // 날짜별 재생
  const closeAt = (ticker: string, idx: number) => collectedSeries(ticker)[idx].close_price
  const points: PerformancePoint[] = []
  for (let idx = startIdx; idx < dates.length; idx += 1) {
    let cash = INITIAL_BALANCE
    const qty = new Map<string, number>()
    for (const { trade, idx: tradeIdx } of applied) {
      if (tradeIdx > idx) continue
      const sign = trade.side === 'buy' ? -1 : 1
      cash += sign * trade.price * trade.quantity
      qty.set(trade.ticker, (qty.get(trade.ticker) ?? 0) + (trade.side === 'buy' ? 1 : -1) * trade.quantity)
    }
    let evalAmount = 0
    for (const [ticker, q] of qty) if (q > 0) evalAmount += closeAt(ticker, idx) * q
    points.push({
      date: dates[idx],
      total_asset: round2(cash + evalAmount),
      cash_balance: round2(cash),
      eval_amount: round2(evalAmount),
      daily_return_pct: null,
      cumulative_return_pct: null,
      portfolio_index: 100,
      estimated: false,
      estimated_tickers: [],
    })
  }
  const startValue = points[0].total_asset
  points.forEach((p, i) => {
    p.daily_return_pct = i === 0 ? null : round2((p.total_asset / points[i - 1].total_asset - 1) * 100)
    p.cumulative_return_pct = round2((p.total_asset / startValue - 1) * 100)
    p.portfolio_index = round4((p.total_asset / startValue) * 100)
  })

  const endValue = points.at(-1)!.total_asset
  const values = points.map((p) => ({ date: p.date, value: p.total_asset }))
  const highest = values.reduce((a, b) => (b.value > a.value ? b : a))
  const lowest = values.reduce((a, b) => (b.value < a.value ? b : a))
  const dailyReturns = points.flatMap((p) => (p.daily_return_pct == null ? [] : [p.daily_return_pct]))
  let dailyVol: number | null = null
  if (dailyReturns.length >= 20) {
    const mean = dailyReturns.reduce((s, r) => s + r, 0) / dailyReturns.length
    dailyVol = Math.sqrt(dailyReturns.reduce((s, r) => s + (r - mean) ** 2, 0) / (dailyReturns.length - 1))
  }
  const metrics: PerformanceMetrics = {
    highest: { value: highest.value, date: highest.date },
    lowest: { value: lowest.value, date: lowest.date },
    max_drawdown: maxDrawdown(values),
    daily_volatility_pct: dailyVol == null ? null : round4(dailyVol),
    annualized_volatility_pct: dailyVol == null ? null : round4(dailyVol * Math.sqrt(252)),
    annualized_return_pct: null,
    daily_return_count: dailyReturns.length,
    trading_days: points.length,
    sample_rules: { volatility_min_returns: 20, annualized_return_min_returns: 252 },
  }

  // 지수 비교: 첫 공통 거래일을 양쪽 모두 100으로 맞춘다
  const closes = benchmarkCloses(benchmark, dates).slice(startIdx)
  let comparison: BenchmarkComparison
  if (points.length < 2) {
    comparison = {
      status: 'insufficient_overlap',
      benchmark_name: benchName,
      message: pickText(locale, '공통 거래일이 부족해 지수와 비교하지 않았습니다.', 'There are too few common trading days to compare with the index.'),
      points: [],
    }
  } else {
    const bStart = closes[0]
    const cmpPoints: BenchmarkPoint[] = points.map((p, i) => {
      const benchIndex = round4((closes[i] / bStart) * 100)
      const portRet = round2(p.portfolio_index - 100)
      const benchRet = round2(benchIndex - 100)
      return {
        date: p.date,
        portfolio_index: p.portfolio_index,
        benchmark_index: benchIndex,
        benchmark_close: closes[i],
        portfolio_return_pct: portRet,
        benchmark_return_pct: benchRet,
        excess_return_pp: round2(portRet - benchRet),
        estimated: false,
      }
    })
    const last = cmpPoints.at(-1)!
    comparison = {
      status: 'ok',
      message: null,
      benchmark_code: benchmark,
      benchmark_name: benchName,
      start_date: points[0].date,
      end_date: last.date,
      common_days: points.length,
      start_aligned: false,
      benchmark_start_value: bStart,
      benchmark_end_value: closes.at(-1)!,
      benchmark_last_date: dataDate,
      benchmark_lag_business_days: lag,
      portfolio_return_pct: last.portfolio_return_pct,
      benchmark_return_pct: last.benchmark_return_pct,
      excess_return_pp: last.excess_return_pp,
      points: cmpPoints,
      total_points: cmpPoints.length,
      sampled: false,
    }
  }

  return {
    ...base,
    status: 'ok',
    message: null,
    requested_start_date: requestedStartDate,
    start_date: points[0].date,
    end_date: points.at(-1)!.date,
    summary: {
      current_cash: store.cashBalance,
      start_value: startValue,
      end_value: endValue,
      profit_loss: round2(endValue - startValue),
      period_return_pct: round2((endValue / startValue - 1) * 100),
      cumulative_return_pct: round2((endValue / INITIAL_BALANCE - 1) * 100),
      eval_amount: points.at(-1)!.eval_amount,
      cash_balance: points.at(-1)!.cash_balance,
    },
    metrics,
    series: { points, total_points: points.length, sampled: false, max_points: 400 },
    comparison,
    warnings,
  }
}

export const performanceMock: PerformanceApi = { getPerformance }
