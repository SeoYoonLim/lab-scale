import { z } from 'zod'
import type { AnalysisType, DateGroup } from './common'
import { analysisTypeSchema, dateGroupSchema } from './common'

// GET /api/history/search (docs/FRONTEND_HANDOFF_I18N_HISTORY.md 3-2절): 리서치·진단 통합 검색 + 탭 숫자(counts).
// 상세는 항목의 detail_path(기존 상세 API, 리서치는 ?view=record)로 연다. 브리핑은 이 검색에 안 나온다.

export type HistoryType = 'all' | 'research' | 'diagnosis'
export type HistorySort = 'created_at,desc' | 'created_at,asc'

export interface HistoryResearchPart {
  report_id: number
  previous_report_id: number | null
  question: string
  analysis_type: AnalysisType
  analysis_types: AnalysisType[]
  source_count: number
  used_tools: string[]
}

export interface HistoryTopHolding {
  ticker: string | null
  company_name: string | null
  weight_pct: number | null
}

export interface HistoryFlagBrief {
  code: string
  ticker: string | null
  company_name: string | null
  market: string | null
}

export interface HistoryDiagnosisPart {
  diagnosis_id: number
  source: 'llm' | 'rule_based'
  total_asset: number
  holdings_count: number
  cash_weight_pct: number | null
  top_holdings: HistoryTopHolding[]
  risk_summary: string | null
  flags: HistoryFlagBrief[]
  flag_codes: string[]
  generated_at: string | null
}

export interface HistoryItem {
  kind: 'research' | 'diagnosis'
  id: number
  created_at: string
  // 요청 시간대 기준 표시값. created_at은 UTC라서 화면에는 이걸 쓴다(핸드오프 15-3절).
  local_date: string
  local_time: string
  date_group: DateGroup
  title: string
  summary: string | null
  locale: string
  company_names: string[]
  tickers: string[]
  detail_path: string
  // kind에 해당하는 쪽만 채워지고 나머지는 null
  research: HistoryResearchPart | null
  diagnosis: HistoryDiagnosisPart | null
}

export interface HistoryCounts {
  all: number
  research: number
  diagnosis: number
}

export interface HistorySearchResponse {
  type: HistoryType
  keyword: string | null
  // 공백으로 나눈 검색 낱말(최대 5개) - 결과에서 강조할 때 쓴다
  terms: string[]
  date_from: string | null
  date_to: string | null
  timezone: string
  sort: string
  page: number
  size: number
  total: number
  total_pages: number
  has_next: boolean
  counts: HistoryCounts
  items: HistoryItem[]
}

export interface HistorySearchParams {
  type?: HistoryType
  keyword?: string
  date_from?: string
  date_to?: string
  timezone?: string
  page?: number
  size?: number
  sort?: HistorySort
}

const ns = z.string().nullish().transform((v) => v ?? null)
const nn = z.number().nullish().transform((v) => v ?? null)

const researchPartSchema = z.object({
  report_id: z.number(),
  previous_report_id: nn,
  question: z.string(),
  analysis_type: analysisTypeSchema,
  analysis_types: z.array(analysisTypeSchema).default([]),
  source_count: z.number(),
  used_tools: z.array(z.string()).default([]),
}) satisfies z.ZodType<HistoryResearchPart, unknown>

const diagnosisPartSchema = z.object({
  diagnosis_id: z.number(),
  source: z.enum(['llm', 'rule_based']),
  total_asset: z.number(),
  holdings_count: z.number(),
  cash_weight_pct: nn,
  top_holdings: z
    .array(z.object({ ticker: ns, company_name: ns, weight_pct: nn }))
    .default([]),
  risk_summary: ns,
  flags: z
    .array(z.object({ code: z.string(), ticker: ns, company_name: ns, market: ns }))
    .default([]),
  flag_codes: z.array(z.string()).default([]),
  generated_at: ns,
}) satisfies z.ZodType<HistoryDiagnosisPart, unknown>

export const historySearchSchema = z.object({
  type: z.enum(['all', 'research', 'diagnosis']),
  keyword: ns,
  terms: z.array(z.string()).default([]),
  date_from: ns,
  date_to: ns,
  timezone: z.string(),
  sort: z.string(),
  page: z.number(),
  size: z.number(),
  total: z.number(),
  total_pages: z.number(),
  has_next: z.boolean(),
  counts: z.object({ all: z.number(), research: z.number(), diagnosis: z.number() }),
  items: z.array(
    z.object({
      kind: z.enum(['research', 'diagnosis']),
      id: z.number(),
      created_at: z.string(),
      local_date: z.string(),
      local_time: z.string(),
      date_group: dateGroupSchema,
      title: z.string(),
      summary: ns,
      locale: z.string(),
      company_names: z.array(z.string()).default([]),
      tickers: z.array(z.string()).default([]),
      detail_path: z.string(),
      research: researchPartSchema.nullish().transform((v) => v ?? null),
      diagnosis: diagnosisPartSchema.nullish().transform((v) => v ?? null),
    }),
  ),
}) satisfies z.ZodType<HistorySearchResponse, unknown>

// 날짜 그룹 헤더는 항목의 date_group이 "바뀔 때만" 새로 그린다(페이지 경계에서 같은 그룹이 이어질 수 있다,
// 핸드오프 I18N_HISTORY 3-1절). 이전 페이지 마지막 항목의 그룹을 알면 넘겨서 이어지는 그룹의 헤더를 건너뛴다.
export function groupHeaderIndexes(items: { date_group: DateGroup }[], previousGroup: DateGroup | null = null): number[] {
  const indexes: number[] = []
  let last = previousGroup
  items.forEach((item, index) => {
    if (item.date_group !== last) indexes.push(index)
    last = item.date_group
  })
  return indexes
}
