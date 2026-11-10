import { z } from 'zod'

// GET /api/portfolio/performance?view=benchmark (docs/FRONTEND_HANDOFF_PORTFOLIO_PERFORMANCE.md, 성과·벤치마크 비교).
// 상태(status)를 먼저 본다: ok·partial일 때만 숫자와 그래프가 있고, 나머지는 수익률이 null이다(0으로 그리지 않는다).

export const PERFORMANCE_PERIODS = ['1m', '3m', '6m', '1y', 'all'] as const
export type PerformancePeriod = (typeof PERFORMANCE_PERIODS)[number]

export const BENCHMARK_CODES = ['KS11', 'KQ11'] as const
export type BenchmarkCode = (typeof BENCHMARK_CODES)[number]

export type PerformanceStatus =
  | 'ok'
  | 'partial'
  | 'no_account'
  | 'no_trades'
  | 'pending_only'
  | 'no_prices'
  | 'history_unavailable'

export type BenchmarkStatus = 'ok' | 'partial' | 'no_data' | 'insufficient_overlap' | 'portfolio_unavailable'

export interface PerformanceSummary {
  current_cash: number | null
  start_value: number | null
  end_value: number | null
  profit_loss: number | null
  period_return_pct: number | null
  cumulative_return_pct: number | null
  // 아래 둘은 응답에 있을 때만(옛 상태에서는 생략될 수 있다)
  eval_amount?: number | null
  cash_balance?: number | null
}

export interface DatedValue {
  value: number
  date: string
}

export interface MaxDrawdown {
  pct: number
  peak_date: string | null
  trough_date: string | null
  recovery_date: string | null
  duration_days: number
}

export interface PerformanceMetrics {
  highest: DatedValue | null
  lowest: DatedValue | null
  max_drawdown: MaxDrawdown | null
  daily_volatility_pct: number | null
  annualized_volatility_pct: number | null
  annualized_return_pct: number | null
  daily_return_count: number
  trading_days: number
  sample_rules: { volatility_min_returns: number; annualized_return_min_returns: number }
}

export interface PerformancePoint {
  date: string
  total_asset: number
  cash_balance: number
  eval_amount: number
  daily_return_pct: number | null
  cumulative_return_pct: number | null
  // 그 기간 시작 = 100으로 맞춘 값
  portfolio_index: number
  // 시세가 빠진 날을 직전 종가로 평가했는가(점선·흐린 점으로 구분)
  estimated: boolean
  estimated_tickers: string[]
}

export interface PerformanceSeries {
  points: PerformancePoint[]
  total_points: number
  // 400개를 넘으면 고르게 줄인다(첫·끝·최고·최저·낙폭 고점과 저점은 남긴다)
  sampled: boolean
  max_points: number
}

export interface BenchmarkPoint {
  date: string
  portfolio_index: number
  benchmark_index: number
  benchmark_close: number
  portfolio_return_pct: number
  benchmark_return_pct: number
  excess_return_pp: number
  estimated: boolean
}

export interface BenchmarkComparison {
  status: BenchmarkStatus
  message: string | null
  benchmark_name: string
  benchmark_code?: string
  points: BenchmarkPoint[]
  // 비교 구간 정보와 수익률은 ok·partial일 때만 있다
  start_date?: string | null
  end_date?: string | null
  common_days?: number
  start_aligned?: boolean
  benchmark_start_value?: number | null
  benchmark_end_value?: number | null
  benchmark_last_date?: string | null
  benchmark_lag_business_days?: number | null
  portfolio_return_pct?: number | null
  benchmark_return_pct?: number | null
  excess_return_pp?: number | null
  total_points?: number
  sampled?: boolean
}

export type PerformanceWarningKind =
  | 'data_delayed'
  | 'history_mismatch'
  | 'pending_trades'
  | 'period_before_inception'
  | 'estimated_prices'
  | 'price_discontinuity'
  | 'benchmark_no_data'
  | 'benchmark_insufficient_overlap'
  | 'benchmark_missing_days'
  | 'benchmark_start_aligned'

export interface PerformanceWarning {
  // 새 종류가 생겨도 화면이 깨지지 않게 문자열로 받고, message를 그대로 보여준다.
  kind: string
  message: string
  // 종류마다 값이 더 붙는다(date, lag, count ...). 화면은 message만 쓴다.
  [extra: string]: unknown
}

export interface PerformanceView {
  generated_at: string
  calc_version: string
  locale: string
  status: PerformanceStatus
  message: string | null
  history_verified: boolean
  period: PerformancePeriod
  requested_start_date: string | null
  start_date: string | null
  end_date: string | null
  data_date: string | null
  expected_date: string
  data_lag_business_days: number | null
  initial_balance: number
  benchmark_code: BenchmarkCode
  benchmark_name: string
  summary: PerformanceSummary
  metrics: PerformanceMetrics
  series: PerformanceSeries
  comparison: BenchmarkComparison
  estimated_dates: string[]
  cash_flows: { supported: boolean; external_flow_count: number; return_method: string }
  price_basis: { source: string; dividends_included: boolean; corporate_actions_adjusted: string }
  warnings: PerformanceWarning[]
  disclaimer: string
}

export interface PerformanceParams {
  period?: PerformancePeriod
  benchmark?: BenchmarkCode
  locale?: string
}

const performanceStatusSchema = z.enum([
  'ok',
  'partial',
  'no_account',
  'no_trades',
  'pending_only',
  'no_prices',
  'history_unavailable',
])
const benchmarkStatusSchema = z.enum(['ok', 'partial', 'no_data', 'insufficient_overlap', 'portfolio_unavailable'])
const periodSchema = z.enum(PERFORMANCE_PERIODS)
const benchmarkCodeSchema = z.enum(BENCHMARK_CODES)

const datedValueSchema = z.object({ value: z.number(), date: z.string() })

const maxDrawdownSchema = z.object({
  pct: z.number(),
  peak_date: z.string().nullable(),
  trough_date: z.string().nullable(),
  recovery_date: z.string().nullable(),
  duration_days: z.number(),
})

const performanceSummarySchema = z.object({
  current_cash: z.number().nullable(),
  start_value: z.number().nullable(),
  end_value: z.number().nullable(),
  profit_loss: z.number().nullable(),
  period_return_pct: z.number().nullable(),
  cumulative_return_pct: z.number().nullable(),
  eval_amount: z.number().nullish(),
  cash_balance: z.number().nullish(),
}) satisfies z.ZodType<PerformanceSummary>

const performanceMetricsSchema = z.object({
  highest: datedValueSchema.nullable(),
  lowest: datedValueSchema.nullable(),
  max_drawdown: maxDrawdownSchema.nullable(),
  daily_volatility_pct: z.number().nullable(),
  annualized_volatility_pct: z.number().nullable(),
  annualized_return_pct: z.number().nullable(),
  daily_return_count: z.number(),
  trading_days: z.number(),
  sample_rules: z.object({ volatility_min_returns: z.number(), annualized_return_min_returns: z.number() }),
}) satisfies z.ZodType<PerformanceMetrics>

const performancePointSchema = z.object({
  date: z.string(),
  total_asset: z.number(),
  cash_balance: z.number(),
  eval_amount: z.number(),
  daily_return_pct: z.number().nullable(),
  cumulative_return_pct: z.number().nullable(),
  portfolio_index: z.number(),
  estimated: z.boolean(),
  estimated_tickers: z.array(z.string()),
}) satisfies z.ZodType<PerformancePoint>

const benchmarkPointSchema = z.object({
  date: z.string(),
  portfolio_index: z.number(),
  benchmark_index: z.number(),
  benchmark_close: z.number(),
  portfolio_return_pct: z.number(),
  benchmark_return_pct: z.number(),
  excess_return_pp: z.number(),
  estimated: z.boolean(),
}) satisfies z.ZodType<BenchmarkPoint>

const benchmarkComparisonSchema = z.object({
  status: benchmarkStatusSchema,
  message: z.string().nullable(),
  benchmark_name: z.string(),
  benchmark_code: z.string().optional(),
  points: z.array(benchmarkPointSchema),
  start_date: z.string().nullish(),
  end_date: z.string().nullish(),
  common_days: z.number().optional(),
  start_aligned: z.boolean().optional(),
  benchmark_start_value: z.number().nullish(),
  benchmark_end_value: z.number().nullish(),
  benchmark_last_date: z.string().nullish(),
  benchmark_lag_business_days: z.number().nullish(),
  portfolio_return_pct: z.number().nullish(),
  benchmark_return_pct: z.number().nullish(),
  excess_return_pp: z.number().nullish(),
  total_points: z.number().optional(),
  sampled: z.boolean().optional(),
}) satisfies z.ZodType<BenchmarkComparison>

const performanceWarningSchema = z
  .object({ kind: z.string(), message: z.string() })
  .passthrough() as unknown as z.ZodType<PerformanceWarning>

export const performanceViewSchema = z.object({
  generated_at: z.string(),
  calc_version: z.string(),
  locale: z.string(),
  status: performanceStatusSchema,
  message: z.string().nullable(),
  history_verified: z.boolean(),
  period: periodSchema,
  requested_start_date: z.string().nullable(),
  start_date: z.string().nullable(),
  end_date: z.string().nullable(),
  data_date: z.string().nullable(),
  expected_date: z.string(),
  data_lag_business_days: z.number().nullable(),
  initial_balance: z.number(),
  benchmark_code: benchmarkCodeSchema,
  benchmark_name: z.string(),
  summary: performanceSummarySchema,
  metrics: performanceMetricsSchema,
  series: z.object({
    points: z.array(performancePointSchema),
    total_points: z.number(),
    sampled: z.boolean(),
    max_points: z.number(),
  }),
  comparison: benchmarkComparisonSchema,
  estimated_dates: z.array(z.string()),
  cash_flows: z.object({ supported: z.boolean(), external_flow_count: z.number(), return_method: z.string() }),
  price_basis: z.object({
    source: z.string(),
    dividends_included: z.boolean(),
    corporate_actions_adjusted: z.string(),
  }),
  warnings: z.array(performanceWarningSchema),
  disclaimer: z.string(),
}) satisfies z.ZodType<PerformanceView>

// 숫자와 그래프를 보여줘도 되는 상태(핸드오프 8절): ok·partial만. 나머지는 message 안내만.
export function hasPerformanceNumbers(status: PerformanceStatus): boolean {
  return status === 'ok' || status === 'partial'
}

export function hasBenchmarkChart(status: BenchmarkStatus): boolean {
  return status === 'ok' || status === 'partial'
}
