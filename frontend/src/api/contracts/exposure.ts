import { z } from 'zod'

// GET /api/portfolio/exposure?view=look_through, 그리고 진단 응답의 look_through 스냅샷
// (docs/FRONTEND_HANDOFF_ETF_EXPOSURE.md). 비중(*_weight_pct)의 분모는 총자산(현금 포함)이다.
// 예전 기본 응답의 effective_weight_pct(보유 평가액 대비)와 섞지 않는다.

export type ExposureStatus = 'ok' | 'empty' | 'no_prices'
// 진단 스냅샷에서만 'unavailable'(계산 실패, 진단의 나머지는 정상)이 더 있다.
export type SnapshotStatus = ExposureStatus | 'unavailable'
export type ExposureType = 'direct' | 'etf' | 'both'
export type EtfDecomposeStatus = 'decomposed' | 'not_decomposed'

export interface LookThroughTop1 {
  name: string
  code: string | null
  total_weight_pct: number
}

export interface LookThroughSummary {
  cash_balance: number
  holdings_value: number
  total_asset: number
  stock_amount: number
  etf_amount: number
  direct_amount: number
  via_etf_amount: number
  companies_amount: number
  linked_via_amount: number
  unlinked_via_amount: number
  nested_etf_amount: number
  residual_amount: number
  undecomposed_etf_amount: number
  unidentified_amount: number
  unidentified_weight_pct: number | null
  over_allocated_amount: number
  cash_weight_pct: number | null
  companies_weight_pct: number | null
  holding_count: number
  priced_holding_count: number
  unpriced_count: number
  company_count: number
  direct_top1_weight_pct: number | null
  look_through_top1: LookThroughTop1 | null
  // 진단 스냅샷에만 있다(기존 진단의 "상위 1종목 비중", 직접 보유 기준)
  holding_top1_weight_pct?: number | null
}

export interface ViaEtf {
  ticker: string | null
  name: string
  amount: number
  constituent_weight_pct: number
  asset_weight_pct: number
}

export interface CompanyExposure {
  rank: number
  name: string
  code: string | null
  // 우리 종목 목록에 없는 구성종목(해외 주식·채권·CP·스왑 등)이면 null
  ticker: string | null
  linked: boolean
  sector: string | null
  exposure_type: ExposureType
  direct_amount: number
  via_etf_amount: number
  total_amount: number
  direct_weight_pct: number
  via_etf_weight_pct: number
  total_weight_pct: number
  holdings_weight_pct: number | null
  via_etfs: ViaEtf[]
}

export interface Constituent {
  name: string
  code: string | null
  linked_ticker: string | null
  weight_pct: number
  // 분해하지 않은 ETF(레버리지·인버스 등)는 null(공시 비중만)
  amount: number | null
  kind?: 'company' | 'nested_etf'
}

export interface EtfContribution {
  ticker: string
  name: string
  eval_amount: number
  asset_weight_pct: number
  status: EtfDecomposeStatus
  // leveraged_inverse | no_holdings_data | invalid_weights (모르는 값도 문자열로 받는다)
  reason_code: string | null
  leverage_kind: string | null
  notice: string | null
  holdings_as_of: string | null
  holdings_lag_business_days: number | null
  holdings_stale: boolean
  constituent_count: number
  linked_count: number
  unlinked_count: number
  nested_etf_count: number
  positive_weight_sum_pct: number | null
  negative_weight_count: number
  negative_weight_sum_pct: number
  zero_weight_count: number
  null_weight_count: number
  over_100_count: number
  duplicate_count: number
  allocated_amount: number | null
  nested_etf_amount: number | null
  residual_amount: number | null
  constituents: Constituent[]
  nested_etfs: Constituent[]
}

export interface UnpricedHolding {
  ticker: string
  name: string
  asset_type: 'stock' | 'etf' | null
  quantity: number
}

export interface SectorExposure {
  status: 'available' | 'partial' | 'unavailable'
  covered_weight_pct: number | null
  // 업종이 있는 기업이 거의 없어 지금은 대부분 unavailable이고 items는 []다.
  items: { sector: string; amount: number }[]
  message?: string | null
}

export interface ExposureFlag {
  // concentration | etf_inflates_weight | single_etf_exposure (새 종류도 message로 그대로 보여준다)
  kind: string
  message: string
  name?: string | null
  code?: string | null
  etf_ticker?: string | null
  etf_name?: string | null
  value_pct?: number | null
  threshold_pct?: number | null
}

export interface ExposureDataWarning {
  // no_price | older_price | etf_leveraged_inverse | etf_no_holdings_data | etf_invalid_weights | etf_weight_under_100 ...
  kind: string
  message: string
  ticker?: string | null
  name?: string | null
  date?: string | null
}

export interface LookThroughView {
  generated_at: string
  calc_version: string
  locale: string
  status: ExposureStatus
  message: string | null
  has_account: boolean
  valuation_date: string | null
  expected_date: string
  price_lag_business_days: number | null
  stale: boolean
  stale_notice: string | null
  etf_holdings_stale: boolean
  limit: number
  summary: LookThroughSummary
  companies: CompanyExposure[]
  overlaps: CompanyExposure[]
  etfs: EtfContribution[]
  unpriced: UnpricedHolding[]
  sector: SectorExposure
  flags: ExposureFlag[]
  data_warnings: ExposureDataWarning[]
  disclaimer: string
}

// 진단 저장본의 스냅샷(POST /api/portfolio/diagnosis, GET /diagnosis/{id}의 look_through).
// 진단 당시 값 그대로라 다시 열어도 바뀌지 않는다. 이 기능 전에 만든 진단은 look_through 자체가 null이다.
export interface LookThroughSnapshot {
  calc_version: string
  status: SnapshotStatus
  valuation_date: string | null
  expected_date: string | null
  price_lag_business_days: number | null
  etf_holdings_stale: boolean
  summary: LookThroughSummary
  companies: CompanyExposure[]
  overlaps: CompanyExposure[]
  etfs: EtfContribution[]
  unpriced: UnpricedHolding[]
  sector: SectorExposure
  flags: ExposureFlag[]
  data_warnings: ExposureDataWarning[]
  computed_at?: string | null
  valuation_basis?: string | null
}

export interface ExposureParams {
  limit?: number
  locale?: string
}

const nullableNumber = z.number().nullable()

const lookThroughTop1Schema = z.object({
  name: z.string(),
  code: z.string().nullable(),
  total_weight_pct: z.number(),
}) satisfies z.ZodType<LookThroughTop1>

export const lookThroughSummarySchema = z.object({
  cash_balance: z.number(),
  holdings_value: z.number(),
  total_asset: z.number(),
  stock_amount: z.number(),
  etf_amount: z.number(),
  direct_amount: z.number(),
  via_etf_amount: z.number(),
  companies_amount: z.number(),
  linked_via_amount: z.number(),
  unlinked_via_amount: z.number(),
  nested_etf_amount: z.number(),
  residual_amount: z.number(),
  undecomposed_etf_amount: z.number(),
  unidentified_amount: z.number(),
  unidentified_weight_pct: nullableNumber,
  over_allocated_amount: z.number(),
  cash_weight_pct: nullableNumber,
  companies_weight_pct: nullableNumber,
  holding_count: z.number(),
  priced_holding_count: z.number(),
  unpriced_count: z.number(),
  company_count: z.number(),
  direct_top1_weight_pct: nullableNumber,
  look_through_top1: lookThroughTop1Schema.nullable(),
  holding_top1_weight_pct: nullableNumber.optional(),
}) satisfies z.ZodType<LookThroughSummary>

const viaEtfSchema = z.object({
  ticker: z.string().nullable(),
  name: z.string(),
  amount: z.number(),
  constituent_weight_pct: z.number(),
  asset_weight_pct: z.number(),
}) satisfies z.ZodType<ViaEtf>

export const companyExposureSchema = z.object({
  rank: z.number(),
  name: z.string(),
  code: z.string().nullable(),
  ticker: z.string().nullable(),
  linked: z.boolean(),
  sector: z.string().nullable(),
  exposure_type: z.enum(['direct', 'etf', 'both']),
  direct_amount: z.number(),
  via_etf_amount: z.number(),
  total_amount: z.number(),
  direct_weight_pct: z.number(),
  via_etf_weight_pct: z.number(),
  total_weight_pct: z.number(),
  holdings_weight_pct: nullableNumber,
  via_etfs: z.array(viaEtfSchema),
}) satisfies z.ZodType<CompanyExposure>

const constituentSchema = z.object({
  name: z.string(),
  code: z.string().nullable(),
  linked_ticker: z.string().nullable(),
  weight_pct: z.number(),
  amount: nullableNumber,
  kind: z.enum(['company', 'nested_etf']).optional(),
}) satisfies z.ZodType<Constituent>

export const etfContributionSchema = z.object({
  ticker: z.string(),
  name: z.string(),
  eval_amount: z.number(),
  asset_weight_pct: z.number(),
  status: z.enum(['decomposed', 'not_decomposed']),
  reason_code: z.string().nullable(),
  leverage_kind: z.string().nullable(),
  notice: z.string().nullable(),
  holdings_as_of: z.string().nullable(),
  holdings_lag_business_days: nullableNumber,
  holdings_stale: z.boolean(),
  constituent_count: z.number(),
  linked_count: z.number(),
  unlinked_count: z.number(),
  nested_etf_count: z.number(),
  positive_weight_sum_pct: nullableNumber,
  negative_weight_count: z.number(),
  negative_weight_sum_pct: z.number(),
  zero_weight_count: z.number(),
  null_weight_count: z.number(),
  over_100_count: z.number(),
  duplicate_count: z.number(),
  allocated_amount: nullableNumber,
  nested_etf_amount: nullableNumber,
  residual_amount: nullableNumber,
  constituents: z.array(constituentSchema),
  nested_etfs: z.array(constituentSchema),
}) satisfies z.ZodType<EtfContribution>

const unpricedSchema = z.object({
  ticker: z.string(),
  name: z.string(),
  asset_type: z.enum(['stock', 'etf']).nullable(),
  quantity: z.number(),
}) satisfies z.ZodType<UnpricedHolding>

const sectorSchema = z.object({
  status: z.enum(['available', 'partial', 'unavailable']),
  covered_weight_pct: nullableNumber,
  items: z.array(z.object({ sector: z.string(), amount: z.number() })),
  message: z.string().nullish(),
}) satisfies z.ZodType<SectorExposure>

// 새 종류의 플래그·경고가 생겨도 검증에서 막히지 않게 kind는 문자열, 나머지 값은 있으면 받는다.
const exposureFlagSchema = z.looseObject({
  kind: z.string(),
  message: z.string(),
  name: z.string().nullish(),
  code: z.string().nullish(),
  etf_ticker: z.string().nullish(),
  etf_name: z.string().nullish(),
  value_pct: z.number().nullish(),
  threshold_pct: z.number().nullish(),
}) satisfies z.ZodType<ExposureFlag>

const exposureDataWarningSchema = z.looseObject({
  kind: z.string(),
  message: z.string(),
  ticker: z.string().nullish(),
  name: z.string().nullish(),
  date: z.string().nullish(),
}) satisfies z.ZodType<ExposureDataWarning>

const exposureBodyShape = {
  summary: lookThroughSummarySchema,
  companies: z.array(companyExposureSchema),
  overlaps: z.array(companyExposureSchema),
  etfs: z.array(etfContributionSchema),
  unpriced: z.array(unpricedSchema),
  sector: sectorSchema,
  flags: z.array(exposureFlagSchema),
  data_warnings: z.array(exposureDataWarningSchema),
}

export const lookThroughViewSchema = z.object({
  generated_at: z.string(),
  calc_version: z.string(),
  locale: z.string(),
  status: z.enum(['ok', 'empty', 'no_prices']),
  message: z.string().nullable(),
  has_account: z.boolean(),
  valuation_date: z.string().nullable(),
  expected_date: z.string(),
  price_lag_business_days: nullableNumber,
  stale: z.boolean(),
  stale_notice: z.string().nullable(),
  etf_holdings_stale: z.boolean(),
  limit: z.number(),
  ...exposureBodyShape,
  disclaimer: z.string(),
}) satisfies z.ZodType<LookThroughView>

export const lookThroughSnapshotSchema = z.object({
  calc_version: z.string(),
  status: z.enum(['ok', 'empty', 'no_prices', 'unavailable']),
  valuation_date: z.string().nullable(),
  expected_date: z.string().nullable(),
  price_lag_business_days: nullableNumber,
  etf_holdings_stale: z.boolean(),
  ...exposureBodyShape,
  computed_at: z.string().nullish(),
  valuation_basis: z.string().nullish(),
}) satisfies z.ZodType<LookThroughSnapshot>

// 기업 노출 합계(직접+간접)와 미확인 노출, 현금을 한 줄 막대로 그릴 때 쓰는 구간(총자산 대비 %).
// 기업 비중을 다 더해도 100%가 안 되는 것이 정상이고, 부족한 몫은 "미확인"이다(핸드오프 1절 4번).
export interface ExposureBarSegment {
  key: 'companies' | 'unidentified' | 'cash'
  weightPct: number
}

export function exposureBarSegments(summary: LookThroughSummary): ExposureBarSegment[] {
  const segments: ExposureBarSegment[] = [
    { key: 'companies', weightPct: summary.companies_weight_pct ?? 0 },
    { key: 'unidentified', weightPct: summary.unidentified_weight_pct ?? 0 },
    { key: 'cash', weightPct: summary.cash_weight_pct ?? 0 },
  ]
  return segments.filter((s) => s.weightPct > 0)
}
