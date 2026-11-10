import { z } from 'zod'
import type { DateGroup, MarketDataStatus, SectionStatus } from './common'
import { dateGroupSchema } from './common'

// 개인화 일일 투자 브리핑 (docs/FRONTEND_HANDOFF_DAILY_BRIEFING.md).
//   POST /api/briefings/daily/generate (201 새로 만듦 / 200 저장본), GET /current(없으면 404), GET /history, GET /{id}
// 저장본은 불변 스냅샷이라 과거 브리핑을 열어도 현재 시세로 다시 계산하지 않는다.
// content는 OpenAPI에서 형식이 열려 있어(Record<string, unknown>) 핸드오프 문서의 실제 응답 예시를 기준으로 잡았다.
// 영역 안의 세부 필드는 대부분 빠질 수 있다고 보고(nullish) 화면에서 방어적으로 읽는다.

export type BriefingSectionKey = 'market' | 'holdings' | 'watchlist' | 'risk' | 'checklist'
export const BRIEFING_SECTION_KEYS: BriefingSectionKey[] = ['market', 'holdings', 'watchlist', 'risk', 'checklist']

export interface BriefingNewsItem {
  url: string | null
  title: string
  source: string | null
  // 오프셋이 붙은 ISO. news·disclosure·market_news는 +09:00, indirect_news(업종·산업 간접 뉴스)는 +00:00(UTC)이다.
  // 문자열 앞 10자를 날짜로 쓰지 않고 Date로 읽어 보여준다(핸드오프 15-3절).
  published_at: string | null
  // 2 이상이면 "비슷한 기사 n건" 배지
  similar_count: number
}

export interface BriefingDisclosure {
  title: string
  source_url: string | null
  // 공시는 날짜만 아는 값이라 T00:00:00+09:00다 - 시각을 보여주지 않고 KST 날짜만 쓴다.
  disclosed_at: string | null
  disclosure_type: string | null
}

export interface BriefingFlag {
  // 진단 규칙 플래그 코드(CONCENTRATION_HIGH ...) 또는 브리핑 종류(day_move ...)
  code?: string
  kind?: string
  text: string
  value?: number | null
  threshold?: number | null
  ticker?: string | null
  company_name?: string | null
}

export interface MarketIndexQuote {
  // kospi | kosdaq | usd_krw
  code: string
  name: string
  // 데이터가 없으면 date: null이고 text는 "코스닥: 데이터 없음"
  date: string | null
  close: number | null
  change_pct: number | null
  week_change_pct: number | null
  lag_business_days: number | null
  text: string
}

export interface MarketSection {
  title: string
  status: SectionStatus
  indices: MarketIndexQuote[]
  news: BriefingNewsItem[]
  news_label: string | null
  news_empty_text: string | null
}

export interface IndirectNews {
  // sector | index
  kind: string
  label: string
  topic: string | null
  items: BriefingNewsItem[]
}

export interface HoldingBriefingItem {
  ticker: string
  name: string
  asset_type: 'stock' | 'etf' | null
  latest_date: string | null
  lag_business_days: number | null
  latest_close: number | null
  day_change_pct: number | null
  week_change_pct: number | null
  return_20d_pct: number | null
  volume_ratio: number | null
  quantity: number | null
  avg_price: number | null
  eval_amount: number | null
  weight_pct: number | null
  profit_loss_pct: number | null
  price_text: string | null
  position_text: string | null
  risk_factors: string[]
  notes: string[]
  news_label: string | null
  news: BriefingNewsItem[]
  news_empty_text: string | null
  indirect_news: IndirectNews | null
  disclosures: BriefingDisclosure[]
  disclosure_empty_text: string | null
}

export interface HoldingsSection {
  title: string
  status: SectionStatus
  message: string | null
  items: HoldingBriefingItem[]
}

export interface SinceResearch {
  last_research_at: string | null
  news_after: number | null
  disclosures_after: number | null
  text: string | null
}

export interface WatchlistBriefingItem {
  ticker: string
  name: string
  latest_date: string | null
  day_change_pct: number | null
  week_change_pct: number | null
  price_text: string | null
  // 규칙 기준 변화(큰 등락·거래량 급증·새 공시)가 있으면 true
  notable: boolean
  changes: string[]
  status_text: string | null
  since_research: SinceResearch | null
  disclosures: BriefingDisclosure[]
  news: BriefingNewsItem[]
}

export interface WatchlistSection {
  title: string
  status: SectionStatus
  message: string | null
  items: WatchlistBriefingItem[]
  notable_count: number | null
  // 보유와 겹쳐 뺀 관심 종목 안내(없으면 null)
  dedup_text: string | null
}

export interface RiskMetrics {
  total_asset: number | null
  holding_count: number | null
  priced_holding_count: number | null
  cash_weight_pct: number | null
  top1_weight_pct: number | null
  top3_weight_pct: number | null
  herfindahl_index: number | null
  market_weights_pct: Record<string, number | null>
}

export interface EtfExposureBrief {
  // computed일 때만 기업별 노출을 보여준다. not_computed면 text(계산하지 않은 이유)만.
  status: 'computed' | 'not_computed' | 'no_etf'
  text: string | null
  flags: { kind: string; text: string; value_pct: number | null; threshold_pct: number | null }[]
  top_companies: { code: string | null; name: string; weight_pct: number }[]
  undecomposed: unknown[]
}

export interface RiskSection {
  title: string
  status: SectionStatus
  // empty일 때의 안내(risk는 texts로 온다)
  message: string | null
  texts: string[]
  metrics: RiskMetrics | null
  flags: BriefingFlag[]
  sector: { status: string; text: string | null } | null
  etf_exposure: EtfExposureBrief | null
  cautions: string[]
}

export interface ChecklistItem {
  // data_delay | data_missing | earnings_disclosure | holding_disclosure | risk_* | watchlist_disclosure
  kind: string
  text: string
  ticker: string | null
  url: string | null
}

export interface ChecklistSection {
  title: string
  status: SectionStatus
  message: string | null
  items: ChecklistItem[]
}

export interface BriefingFreshness {
  status: MarketDataStatus
  // latest가 아닌 모든 경우 true -> 경고 띠
  warning: boolean
  // 맨 위 한 문장
  headline: string
  notices: string[]
  data_as_of: string | null
  expected_date: string | null
  lag_business_days: number | null
  price_status: string | null
  price_status_detail: string | null
  sources: Record<string, { date: string | null; lag_business_days: number | null }>
  news_collected_at: string | null
  disclosure_collected_at: string | null
  last_refresh: { ok: boolean; finished_at: string | null; failed_steps: string[] } | null
  intraday: boolean
}

export interface BriefingSummary {
  text: string
  // llm | code
  source: string
  // "AI가 위 계산 결과만으로 쓴 요약입니다(숫자 검증 통과)." 같은 출처 안내
  note: string | null
}

export interface BriefingContent {
  title: string
  locale: string
  local_date: string
  timezone: string
  generated_at: string
  data_as_of: string | null
  market_data_status: MarketDataStatus
  // 영역 순서는 JSON 키 순서가 아니라 이 배열을 따른다(저장본 JSONB는 키 순서를 보존하지 않는다).
  section_order: string[]
  section_status: Record<string, SectionStatus>
  freshness: BriefingFreshness
  summary: BriefingSummary
  sections: {
    market: MarketSection | null
    holdings: HoldingsSection | null
    watchlist: WatchlistSection | null
    risk: RiskSection | null
    checklist: ChecklistSection | null
  }
  highlights: BriefingFlag[]
  not_advice: string
  disclaimer: string
}

export interface BriefingTicker {
  ticker: string
  name: string
  scope: 'holding' | 'watchlist'
}

export interface BriefingSource {
  // news | disclosure | indirect_news | market_news
  kind: string
  title: string
  ticker: string | null
  url: string | null
  date: string | null
}

export interface DailyBriefing {
  id: number
  version: number
  local_date: string
  timezone: string
  // 만든 당시 언어. 나중에 계정 언어를 바꿔도 바뀌지 않는다.
  locale: string
  generated_at: string
  data_as_of: string | null
  market_data_status: MarketDataStatus
  // llm | code
  summary_source: string
  section_status: Record<string, SectionStatus>
  tickers: BriefingTicker[]
  sources: BriefingSource[]
  content: BriefingContent
  // ko-KR이 아닌 브리핑에만: 같은 계산 결과의 한국어 브리핑("한국어 원문 보기")
  original_content: BriefingContent | null
  date_group: DateGroup | null
  // generate에서만(true=새로 만듦, false=저장본). 상세·현재 조회는 null/생략.
  created: boolean | null
}

export interface DailyBriefingListItem {
  id: number
  version: number
  versions_on_date: number
  local_date: string
  local_time: string
  date_group: DateGroup
  generated_at: string
  locale: string
  data_as_of: string | null
  market_data_status: MarketDataStatus
  summary_source: string
  title: string
  headline: string | null
  summary: string | null
  section_status: Record<string, SectionStatus>
  ticker_count: number
  detail_path: string
}

export interface DailyBriefingHistory {
  items: DailyBriefingListItem[]
  page: number
  size: number
  total: number
  has_next: boolean
  total_pages: number
  timezone: string
  latest_only: boolean
  date_from: string | null
  date_to: string | null
}

export interface GenerateBriefingInput {
  // true면 오늘 브리핑이 있어도 새 버전을 만든다(하루 5개까지, 넘으면 409)
  regenerate?: boolean
  locale?: string
  timezone?: string
  // false면 AI 요약 없이 코드 요약만(빠름)
  use_llm?: boolean
}

export interface BriefingHistoryParams {
  page?: number
  size?: number
  date_from?: string
  date_to?: string
  latest_only?: boolean
  timezone?: string
}

// ---------- zod ----------

const sectionStatusSchema = z.enum(['ok', 'partial', 'no_data', 'empty'])
const marketDataStatusSchema = z.enum(['latest', 'delayed', 'stale', 'no_data'])
const nn = z.number().nullish().transform((v) => v ?? null)
const ns = z.string().nullish().transform((v) => v ?? null)

const newsItemSchema = z.object({
  url: ns,
  title: z.string(),
  source: ns,
  published_at: ns,
  similar_count: z.number().nullish().transform((v) => v ?? 1),
}) satisfies z.ZodType<BriefingNewsItem, unknown>

const disclosureSchema = z.object({
  title: z.string(),
  source_url: ns,
  disclosed_at: ns,
  disclosure_type: ns,
}) satisfies z.ZodType<BriefingDisclosure, unknown>

const flagSchema = z.object({
  code: z.string().optional(),
  kind: z.string().optional(),
  text: z.string(),
  value: z.number().nullish(),
  threshold: z.number().nullish(),
  ticker: z.string().nullish(),
  company_name: z.string().nullish(),
}) satisfies z.ZodType<BriefingFlag>

const quoteSchema = z.object({
  code: z.string(),
  name: z.string(),
  date: ns,
  close: nn,
  change_pct: nn,
  week_change_pct: nn,
  lag_business_days: nn,
  text: z.string(),
}) satisfies z.ZodType<MarketIndexQuote, unknown>

const marketSectionSchema = z.object({
  title: z.string(),
  status: sectionStatusSchema,
  indices: z.array(quoteSchema).default([]),
  news: z.array(newsItemSchema).default([]),
  news_label: ns,
  news_empty_text: ns,
}) satisfies z.ZodType<MarketSection, unknown>

const indirectNewsSchema = z.object({
  kind: z.string(),
  label: z.string(),
  topic: ns,
  items: z.array(newsItemSchema).default([]),
}) satisfies z.ZodType<IndirectNews, unknown>

const holdingItemSchema = z.object({
  ticker: z.string(),
  name: z.string(),
  asset_type: z.enum(['stock', 'etf']).nullish().transform((v) => v ?? null),
  latest_date: ns,
  lag_business_days: nn,
  latest_close: nn,
  day_change_pct: nn,
  week_change_pct: nn,
  return_20d_pct: nn,
  volume_ratio: nn,
  quantity: nn,
  avg_price: nn,
  eval_amount: nn,
  weight_pct: nn,
  profit_loss_pct: nn,
  price_text: ns,
  position_text: ns,
  risk_factors: z.array(z.string()).default([]),
  notes: z.array(z.string()).default([]),
  news_label: ns,
  news: z.array(newsItemSchema).default([]),
  news_empty_text: ns,
  indirect_news: indirectNewsSchema.nullish().transform((v) => v ?? null),
  disclosures: z.array(disclosureSchema).default([]),
  disclosure_empty_text: ns,
}) satisfies z.ZodType<HoldingBriefingItem, unknown>

const holdingsSectionSchema = z.object({
  title: z.string(),
  status: sectionStatusSchema,
  message: ns,
  items: z.array(holdingItemSchema).default([]),
}) satisfies z.ZodType<HoldingsSection, unknown>

const sinceResearchSchema = z.object({
  last_research_at: ns,
  news_after: nn,
  disclosures_after: nn,
  text: ns,
}) satisfies z.ZodType<SinceResearch, unknown>

const watchlistItemSchema = z.object({
  ticker: z.string(),
  name: z.string(),
  latest_date: ns,
  day_change_pct: nn,
  week_change_pct: nn,
  price_text: ns,
  notable: z.boolean().default(false),
  changes: z.array(z.string()).default([]),
  status_text: ns,
  since_research: sinceResearchSchema.nullish().transform((v) => v ?? null),
  disclosures: z.array(disclosureSchema).default([]),
  news: z.array(newsItemSchema).default([]),
}) satisfies z.ZodType<WatchlistBriefingItem, unknown>

const watchlistSectionSchema = z.object({
  title: z.string(),
  status: sectionStatusSchema,
  message: ns,
  items: z.array(watchlistItemSchema).default([]),
  notable_count: nn,
  dedup_text: ns,
}) satisfies z.ZodType<WatchlistSection, unknown>

const riskMetricsSchema = z.object({
  total_asset: nn,
  holding_count: nn,
  priced_holding_count: nn,
  cash_weight_pct: nn,
  top1_weight_pct: nn,
  top3_weight_pct: nn,
  herfindahl_index: nn,
  market_weights_pct: z.record(z.string(), z.number().nullable()).default({}),
}) satisfies z.ZodType<RiskMetrics, unknown>

const etfExposureSchema = z.object({
  status: z.enum(['computed', 'not_computed', 'no_etf']),
  text: ns,
  flags: z
    .array(z.object({ kind: z.string(), text: z.string(), value_pct: nn, threshold_pct: nn }))
    .default([]),
  top_companies: z
    .array(z.object({ code: ns, name: z.string(), weight_pct: z.number() }))
    .default([]),
  undecomposed: z.array(z.unknown()).default([]),
}) satisfies z.ZodType<EtfExposureBrief, unknown>

const riskSectionSchema = z.object({
  title: z.string(),
  status: sectionStatusSchema,
  message: ns,
  texts: z.array(z.string()).default([]),
  metrics: riskMetricsSchema.nullish().transform((v) => v ?? null),
  flags: z.array(flagSchema).default([]),
  sector: z
    .object({ status: z.string(), text: ns })
    .nullish()
    .transform((v) => v ?? null),
  etf_exposure: etfExposureSchema.nullish().transform((v) => v ?? null),
  cautions: z.array(z.string()).default([]),
}) satisfies z.ZodType<RiskSection, unknown>

const checklistSectionSchema = z.object({
  title: z.string(),
  status: sectionStatusSchema,
  message: ns,
  items: z
    .array(z.object({ kind: z.string(), text: z.string(), ticker: ns, url: ns }))
    .default([]),
}) satisfies z.ZodType<ChecklistSection, unknown>

const freshnessSchema = z.object({
  status: marketDataStatusSchema,
  warning: z.boolean(),
  headline: z.string(),
  notices: z.array(z.string()).default([]),
  data_as_of: ns,
  expected_date: ns,
  lag_business_days: nn,
  price_status: ns,
  price_status_detail: ns,
  sources: z
    .record(z.string(), z.object({ date: ns, lag_business_days: nn }))
    .default({}),
  news_collected_at: ns,
  disclosure_collected_at: ns,
  last_refresh: z
    .object({ ok: z.boolean(), finished_at: ns, failed_steps: z.array(z.string()).default([]) })
    .nullish()
    .transform((v) => v ?? null),
  intraday: z.boolean().default(false),
}) satisfies z.ZodType<BriefingFreshness, unknown>

const sectionStatusRecord = z.record(z.string(), sectionStatusSchema)

const contentSchema = z.object({
  title: z.string(),
  locale: z.string(),
  local_date: z.string(),
  timezone: z.string(),
  generated_at: z.string(),
  data_as_of: ns,
  market_data_status: marketDataStatusSchema,
  section_order: z.array(z.string()).default([...BRIEFING_SECTION_KEYS]),
  section_status: sectionStatusRecord.default({}),
  freshness: freshnessSchema,
  summary: z.object({ text: z.string(), source: z.string(), note: ns }),
  sections: z.object({
    market: marketSectionSchema.nullish().transform((v) => v ?? null),
    holdings: holdingsSectionSchema.nullish().transform((v) => v ?? null),
    watchlist: watchlistSectionSchema.nullish().transform((v) => v ?? null),
    risk: riskSectionSchema.nullish().transform((v) => v ?? null),
    checklist: checklistSectionSchema.nullish().transform((v) => v ?? null),
  }),
  highlights: z.array(flagSchema).default([]),
  not_advice: z.string(),
  disclaimer: z.string(),
}) satisfies z.ZodType<BriefingContent, unknown>

export const dailyBriefingSchema = z.object({
  id: z.number(),
  version: z.number(),
  local_date: z.string(),
  timezone: z.string(),
  locale: z.string(),
  generated_at: z.string(),
  data_as_of: ns,
  market_data_status: marketDataStatusSchema,
  summary_source: z.string(),
  section_status: sectionStatusRecord.default({}),
  tickers: z
    .array(z.object({ ticker: z.string(), name: z.string(), scope: z.enum(['holding', 'watchlist']) }))
    .default([]),
  sources: z
    .array(z.object({ kind: z.string(), title: z.string(), ticker: ns, url: ns, date: ns }))
    .default([]),
  content: contentSchema,
  original_content: contentSchema.nullish().transform((v) => v ?? null),
  date_group: dateGroupSchema.nullish().transform((v) => v ?? null),
  created: z.boolean().nullish().transform((v) => v ?? null),
}) satisfies z.ZodType<DailyBriefing, unknown>

export const dailyBriefingHistorySchema = z.object({
  items: z.array(
    z.object({
      id: z.number(),
      version: z.number(),
      versions_on_date: z.number(),
      local_date: z.string(),
      local_time: z.string(),
      date_group: dateGroupSchema,
      generated_at: z.string(),
      locale: z.string(),
      data_as_of: ns,
      market_data_status: marketDataStatusSchema,
      summary_source: z.string(),
      title: z.string(),
      headline: ns,
      summary: ns,
      section_status: sectionStatusRecord.default({}),
      ticker_count: z.number(),
      detail_path: z.string(),
    }),
  ),
  page: z.number(),
  size: z.number(),
  total: z.number(),
  has_next: z.boolean(),
  total_pages: z.number(),
  timezone: z.string(),
  latest_only: z.boolean(),
  date_from: ns,
  date_to: ns,
}) satisfies z.ZodType<DailyBriefingHistory, unknown>

// 경고 띠 색: delayed는 노랑, stale·no_data는 빨강, latest는 띠 없이 작은 안내(핸드오프 6절).
export type FreshnessTone = 'none' | 'warn' | 'danger'
export function freshnessTone(status: MarketDataStatus): FreshnessTone {
  if (status === 'latest') return 'none'
  return status === 'delayed' ? 'warn' : 'danger'
}

// 영역을 section_order대로 돌려준다(모르는 키는 건너뛰고, order에 빠진 알려진 영역은 뒤에 붙인다).
export function orderedSectionKeys(content: BriefingContent): BriefingSectionKey[] {
  const known = new Set<string>(BRIEFING_SECTION_KEYS)
  const fromOrder = content.section_order.filter((k): k is BriefingSectionKey => known.has(k))
  const missing = BRIEFING_SECTION_KEYS.filter((k) => !fromOrder.includes(k))
  return [...fromOrder, ...missing]
}
