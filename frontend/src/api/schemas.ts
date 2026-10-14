// backend/API.md 대로 "항상 이 모양"이라고 믿고 받던 응답을 실제로 검증한다. `satisfies z.ZodType<T>`로
// 각 스키마가 types.ts의 타입과 구조적으로 맞는지 컴파일 타임에도 체크한다 - 둘 중 하나만 바뀌면
// 타입 에러가 난다.
import { z } from 'zod'
import type {
  AssetType,
  AuthResponse,
  Company,
  DiagnosisChanges,
  DiagnosisCompareResponse,
  DiagnosisDetail,
  DiagnosisFlag,
  DiagnosisFlagSummary,
  DiagnosisHistoryItem,
  DiagnosisHistoryList,
  DiagnosisHolding,
  DiagnosisHoldingChange,
  DiagnosisMetrics,
  DiagnosisResponse,
  DiagnosisReturnChange,
  DiagnosisTotalAssetChange,
  EventReaction,
  EventReactionsResponse,
  EventReturn,
  StatusResponse,
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

export const assetTypeSchema = z.enum(['stock', 'etf']) satisfies z.ZodType<AssetType>

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
  asset_type: assetTypeSchema,
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
  asset_type: assetTypeSchema,
  price_date: z.string(),
  window_days: z.number(),
  items: z.array(trendingItemSchema),
}) satisfies z.ZodType<TrendingResponse>

export const watchlistItemSchema = z.object({
  company_id: z.number(),
  ticker: z.string(),
  company_name: z.string(),
  asset_type: assetTypeSchema,
  added_at: z.string(),
  latest_close: z.number().nullable(),
  latest_close_date: z.string().nullable(),
  change_pct: z.number().nullable(),
}) satisfies z.ZodType<WatchlistItem>

export const watchlistListSchema = z.object({ items: z.array(watchlistItemSchema) })

export const holdingSchema = z.object({
  ticker: z.string(),
  company_name: z.string(),
  asset_type: assetTypeSchema,
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
  asset_type: assetTypeSchema,
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
  asset_type: assetTypeSchema,
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
  diagnosis_id: z.number().nullable(),
  created_at: z.string().nullable(),
}) satisfies z.ZodType<DiagnosisResponse>

// GET /api/portfolio/diagnosis/{id} - diagnosis_id 자리에 id가 오는 것만 다르다.
export const diagnosisDetailSchema = diagnosisResponseSchema
  .omit({ diagnosis_id: true })
  .extend({ id: z.number() }) satisfies z.ZodType<DiagnosisDetail>

export const diagnosisFlagSummarySchema = z.object({
  code: z.string(),
  ticker: z.string().nullable(),
  company_name: z.string().nullable(),
  market: z.string().nullable(),
}) satisfies z.ZodType<DiagnosisFlagSummary>

export const diagnosisHistoryItemSchema = z.object({
  id: z.number(),
  created_at: z.string(),
  source: z.enum(['llm', 'rule_based']),
  total_asset: z.number(),
  holdings_count: z.number(),
  flags: z.array(diagnosisFlagSummarySchema),
  summary_preview: z.string(),
}) satisfies z.ZodType<DiagnosisHistoryItem>

export const diagnosisHistoryListSchema = z.object({
  total: z.number(),
  limit: z.number(),
  offset: z.number(),
  items: z.array(diagnosisHistoryItemSchema),
}) satisfies z.ZodType<DiagnosisHistoryList>

export const diagnosisTotalAssetChangeSchema = z.object({
  previous: z.number(),
  current: z.number(),
  change_amount: z.number(),
  change_pct: z.number().nullable(),
}) satisfies z.ZodType<DiagnosisTotalAssetChange>

export const diagnosisHoldingChangeSchema = z.object({
  ticker: z.string(),
  company_name: z.string(),
  status: z.enum(['added', 'removed', 'kept']),
  previous_weight_pct: z.number().nullable(),
  current_weight_pct: z.number().nullable(),
  weight_change_pp: z.number().nullable(),
}) satisfies z.ZodType<DiagnosisHoldingChange>

export const diagnosisReturnChangeSchema = z.object({
  ticker: z.string(),
  company_name: z.string(),
  previous_return_20d_pct: z.number().nullable(),
  current_return_20d_pct: z.number().nullable(),
  change_pp: z.number().nullable(),
}) satisfies z.ZodType<DiagnosisReturnChange>

export const diagnosisChangesSchema = z.object({
  total_asset: diagnosisTotalAssetChangeSchema,
  holdings: z.array(diagnosisHoldingChangeSchema),
  flags: z.object({
    added: z.array(diagnosisFlagSchema),
    resolved: z.array(diagnosisFlagSchema),
  }),
  returns_20d: z.array(diagnosisReturnChangeSchema),
}) satisfies z.ZodType<DiagnosisChanges>

export const diagnosisCompareResponseSchema = z.object({
  current: diagnosisHistoryItemSchema,
  previous: diagnosisHistoryItemSchema.nullable(),
  changes: diagnosisChangesSchema.nullable(),
  disclaimer: z.string(),
}) satisfies z.ZodType<DiagnosisCompareResponse>

export const disclaimerResponseSchema = z.object({ text: z.string() })

export const statusResponseSchema = z.object({
  generated_at: z.string(),
  sources: z.object({
    stock_price: z.object({ latest_date: z.string().nullable() }),
    etf_price: z.object({ latest_date: z.string().nullable() }),
    market_index: z.object({ latest_date: z.string().nullable(), stale: z.boolean() }),
    exchange_rate: z.object({ latest_date: z.string().nullable() }),
    news: z.object({ latest_published_date: z.string().nullable() }),
    disclosure: z.object({ latest_date: z.string().nullable() }),
  }),
  last_refresh: z
    .object({ finished_at: z.string().nullable(), ok: z.boolean(), failed_steps: z.array(z.string()) })
    .nullable(),
  is_realtime: z.boolean(),
  note: z.string(),
}) satisfies z.ZodType<StatusResponse>

const eventReturnSchema = z.object({
  pct: z.number().nullable(),
  index_pct: z.number().nullable(),
  excess_pct: z.number().nullable(),
  available: z.boolean(),
  reason: z.string().nullable(),
}) satisfies z.ZodType<EventReturn>

const eventReactionSchema = z.object({
  event_type: z.enum(['disclosure', 'news']),
  title: z.string(),
  url: z.string().nullable(),
  event_at: z.string().nullable(),
  event_date: z.string().nullable(),
  time_known: z.boolean(),
  base_date: z.string().nullable(),
  base_close: z.number().nullable(),
  reaction_start_date: z.string().nullable(),
  returns: z.object({ d1: eventReturnSchema, d5: eventReturnSchema, d20: eventReturnSchema }),
  volume_ratio: z.number().nullable(),
  volume_sample_days: z.number(),
  other_events_in_window: z.number(),
  note: z.string().nullable(),
}) satisfies z.ZodType<EventReaction>

export const eventReactionsResponseSchema = z.object({
  ticker: z.string(),
  company_name: z.string(),
  asset_type: assetTypeSchema,
  price_as_of: z.string().nullable(),
  market_index: z.string().nullable(),
  events: z.array(eventReactionSchema),
  summary: z.object({
    event_count: z.number(),
    with_d1: z.number(),
    avg_d1_pct: z.number().nullable(),
    positive_d1_count: z.number(),
    with_excess_d1: z.number(),
    avg_excess_d1_pct: z.number().nullable(),
    with_d5: z.number(),
    avg_d5_pct: z.number().nullable(),
    positive_d5_count: z.number(),
    with_excess_d5: z.number(),
    avg_excess_d5_pct: z.number().nullable(),
    with_d20: z.number(),
    avg_d20_pct: z.number().nullable(),
    positive_d20_count: z.number(),
    with_excess_d20: z.number(),
    avg_excess_d20_pct: z.number().nullable(),
  }),
  disclaimer: z.string(),
}) satisfies z.ZodType<EventReactionsResponse>
