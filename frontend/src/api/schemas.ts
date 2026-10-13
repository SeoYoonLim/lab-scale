// backend/API.md 대로 "항상 이 모양"이라고 믿고 받던 응답을 실제로 검증한다. `satisfies z.ZodType<T>`로
// 각 스키마가 types.ts의 타입과 구조적으로 맞는지 컴파일 타임에도 체크한다 - 둘 중 하나만 바뀌면
// 타입 에러가 난다.
import { z } from 'zod'
import type {
  AuthResponse,
  Company,
  DiagnosisFlag,
  DiagnosisHolding,
  DiagnosisMetrics,
  DiagnosisResponse,
  Holding,
  HoldingBrief,
  OrderResult,
  Portfolio,
  ReportDetail,
  ReportList,
  ReportListItem,
  ResearchResponse,
  RealtimePrice,
  Source,
  Trade,
  TradeList,
  TrendingItem,
  TrendingResponse,
  User,
  WatchlistItem,
} from '../types'

export const userSchema = z.object({
  id: z.number(),
  username: z.string(),
}) satisfies z.ZodType<User>

export const authResponseSchema = z.object({
  user: userSchema,
  access_token: z.string(),
  token_type: z.string(),
}) satisfies z.ZodType<AuthResponse>

export const sourceSchema = z.object({
  tool: z.string(),
  type: z.enum(['news', 'disclosure']),
  title: z.string(),
  company_names: z.array(z.string()),
  company_filter: z.string().nullable(),
  url: z.string().nullable(),
}) satisfies z.ZodType<Source>

export const researchResponseSchema = z.object({
  answer: z.string(),
  used_tools: z.array(z.string()),
  sources: z.array(sourceSchema),
  report_id: z.number().nullable(),
  previous_report_id: z.number().nullable(),
  disclaimer: z.string(),
}) satisfies z.ZodType<ResearchResponse>

export const reportListItemSchema = z.object({
  report_id: z.number(),
  previous_report_id: z.number().nullable(),
  question: z.string(),
  summary: z.string().nullable(),
  company_name: z.string().nullable(),
  created_at: z.string(),
  used_tools: z.array(z.string()),
}) satisfies z.ZodType<ReportListItem>

export const reportListSchema = z.object({
  total: z.number(),
  items: z.array(reportListItemSchema),
}) satisfies z.ZodType<ReportList>

export const reportDetailSchema = z.object({
  report_id: z.number(),
  previous_report_id: z.number().nullable(),
  question: z.string(),
  answer: z.string(),
  summary: z.string().nullable(),
  company_name: z.string().nullable(),
  created_at: z.string(),
  used_tools: z.array(z.string()),
  sources: z.array(sourceSchema),
  disclaimer: z.string(),
}) satisfies z.ZodType<ReportDetail>

export const realtimePriceSchema = z.object({
  ticker: z.string(),
  company_name: z.string(),
  current_price: z.number(),
  change_amount: z.number().nullable(),
  change_pct: z.number().nullable(),
  as_of: z.string(),
  queried_at: z.string(),
  is_realtime: z.boolean(),
  source: z.enum(['naver', 'fallback']),
  corrected_from: z.string().nullable(),
}) satisfies z.ZodType<RealtimePrice>

export const companySchema = z.object({
  ticker: z.string(),
  name: z.string(),
  market: z.string().nullable(),
  sector: z.string().nullable(),
  latest_close: z.number().nullable(),
  change_pct: z.number().nullable(),
}) satisfies z.ZodType<Company>

export const companyListSchema = z.array(companySchema)

export const trendingItemSchema = z.object({
  ticker: z.string(),
  company_name: z.string(),
  close_price: z.number(),
  change_pct: z.number().nullable(),
  volume: z.number().nullable(),
  avg_volume: z.number().nullable(),
  volume_ratio: z.number().nullable(),
}) satisfies z.ZodType<TrendingItem>

export const trendingResponseSchema = z.object({
  category: z.enum(['gainers', 'losers', 'volume_surge']),
  price_date: z.string(),
  window_days: z.number(),
  items: z.array(trendingItemSchema),
}) satisfies z.ZodType<TrendingResponse>

export const watchlistItemSchema = z.object({
  company_id: z.number(),
  ticker: z.string(),
  company_name: z.string(),
  added_at: z.string(),
  latest_close: z.number().nullable(),
  latest_close_date: z.string().nullable(),
  change_pct: z.number().nullable(),
}) satisfies z.ZodType<WatchlistItem>

export const watchlistListSchema = z.object({ items: z.array(watchlistItemSchema) })

export const holdingSchema = z.object({
  ticker: z.string(),
  company_name: z.string(),
  quantity: z.number(),
  avg_price: z.number(),
  current_price: z.number().nullable(),
  is_realtime: z.boolean(),
  eval_amount: z.number().nullable(),
  profit_loss: z.number().nullable(),
  profit_loss_pct: z.number().nullable(),
}) satisfies z.ZodType<Holding>

export const portfolioSchema = z.object({
  cash_balance: z.number(),
  holdings: z.array(holdingSchema),
  total_eval_amount: z.number(),
  total_asset: z.number(),
  note: z.string().nullable(),
}) satisfies z.ZodType<Portfolio>

export const orderResultSchema = z.object({
  ticker: z.string(),
  company_name: z.string(),
  side: z.enum(['buy', 'sell']),
  quantity: z.number(),
  price: z.number(),
  executed_at: z.string(),
  cash_balance: z.number(),
  holding: z.object({ quantity: z.number(), avg_price: z.number() }).nullable(),
}) satisfies z.ZodType<OrderResult>

export const tradeSchema = z.object({
  id: z.number(),
  ticker: z.string(),
  company_name: z.string(),
  side: z.enum(['buy', 'sell']),
  quantity: z.number(),
  price: z.number(),
  amount: z.number(),
  executed_at: z.string(),
}) satisfies z.ZodType<Trade>

export const tradeListSchema = z.object({
  total: z.number(),
  limit: z.number(),
  offset: z.number(),
  items: z.array(tradeSchema),
}) satisfies z.ZodType<TradeList>

export const diagnosisHoldingSchema = z.object({
  ticker: z.string(),
  company_name: z.string(),
  market: z.string(),
  quantity: z.number(),
  avg_price: z.number(),
  current_price: z.number().nullable(),
  is_realtime: z.boolean(),
  price_unavailable: z.boolean(),
  eval_amount: z.number().nullable(),
  weight_pct: z.number().nullable(),
  profit_loss: z.number().nullable(),
  profit_loss_pct: z.number().nullable(),
  return_20d_pct: z.number().nullable(),
}) satisfies z.ZodType<DiagnosisHolding>

export const holdingBriefSchema = z.object({
  ticker: z.string(),
  company_name: z.string(),
  profit_loss_pct: z.number().nullable(),
}) satisfies z.ZodType<HoldingBrief>

export const diagnosisMetricsSchema = z.object({
  total_asset: z.number(),
  cash_balance: z.number(),
  cash_weight_pct: z.number().nullable(),
  stock_eval_amount: z.number(),
  holding_count: z.number(),
  priced_holding_count: z.number(),
  top1_weight_pct: z.number().nullable(),
  top3_weight_pct: z.number().nullable(),
  herfindahl_index: z.number().nullable(),
  market_weights_pct: z.record(z.string(), z.number().nullable()),
  total_profit_loss: z.number(),
  total_profit_loss_pct: z.number().nullable(),
  best_holding: holdingBriefSchema.nullable(),
  worst_holding: holdingBriefSchema.nullable(),
}) satisfies z.ZodType<DiagnosisMetrics>

export const diagnosisFlagSchema = z.object({
  code: z.string(),
  message: z.string(),
  value: z.number(),
  threshold: z.number(),
  ticker: z.string().nullable(),
  company_name: z.string().nullable(),
  market: z.string().nullable(),
}) satisfies z.ZodType<DiagnosisFlag>

export const diagnosisResponseSchema = z.object({
  generated_at: z.string(),
  source: z.enum(['llm', 'rule_based']),
  model: z.string().nullable(),
  metrics: diagnosisMetricsSchema,
  holdings: z.array(diagnosisHoldingSchema),
  flags: z.array(diagnosisFlagSchema),
  summary: z.string(),
  strengths: z.array(z.string()),
  risks: z.array(z.string()),
  suggestions: z.array(z.string()),
  notes: z.array(z.string()),
  disclaimer: z.string(),
}) satisfies z.ZodType<DiagnosisResponse>

export const disclaimerResponseSchema = z.object({ text: z.string() })
