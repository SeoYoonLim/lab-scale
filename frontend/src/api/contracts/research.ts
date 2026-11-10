import { z } from 'zod'
import type { AnalysisType, DateGroup, TranslationStatus } from './common'
import { analysisTypeSchema, dateGroupSchema, pageInfoShape, translationStatusSchema } from './common'
import type { Source } from '../../types'

// GET /api/research/{id}?view=record, GET /api/research?view=record (핸드오프 I18N_HISTORY 3-3·3-4절).
// 기본 응답(view 없음)에는 이 필드들이 없다 - 기록 화면만 view=record로 부른다.

// 당시 tool 결과에 남은 기준일. 있는 것만 온다(나머지 null/생략). 화면에는 "분석 기준일"로 보여준다.
export interface DataBasisItem {
  tool: string
  company_name?: string | null
  ticker?: string | null
  period_start_date?: string | null
  period_end_date?: string | null
  base_date?: string | null
  latest_date?: string | null
  data_date?: string | null
  price_as_of?: string | null
  as_of?: string | null
}

export interface ReportRecordItem {
  report_id: number
  previous_report_id: number | null
  question: string
  summary: string | null
  company_name: string | null
  created_at: string
  used_tools: string[]
  title: string
  company_names: string[]
  tickers: string[]
  analysis_type: AnalysisType
  analysis_types: AnalysisType[]
  source_count: number
  local_date: string
  local_time: string
  date_group: DateGroup
  locale: string
}

export interface ReportRecordList {
  total: number
  has_next: boolean
  total_pages: number
  timezone: string
  items: ReportRecordItem[]
}

export interface ReportRecordDetail {
  report_id: number
  previous_report_id: number | null
  question: string
  answer: string
  summary: string | null
  company_name: string | null
  created_at: string
  used_tools: string[]
  sources: Source[]
  disclaimer: string
  title: string
  company_names: string[]
  tickers: string[]
  analysis_type: AnalysisType
  analysis_types: AnalysisType[]
  source_count: number
  local_date: string
  local_time: string
  date_group: DateGroup
  data_basis: DataBasisItem[]
  timezone: string
  locale: string
  translation_status: TranslationStatus | null
  // 번역이 있는 답이면 번역 전 한국어 원문. 한국어 답이거나 원문이 없으면 null.
  original_answer: string | null
}

const sourceSchema = z.object({
  tool: z.string(),
  type: z.enum(['news', 'disclosure']),
  title: z.string(),
  company_names: z.array(z.string()),
  company_filter: z.string().nullable(),
  url: z.string().nullable(),
})

export const dataBasisItemSchema = z.object({
  tool: z.string(),
  company_name: z.string().nullish(),
  ticker: z.string().nullish(),
  period_start_date: z.string().nullish(),
  period_end_date: z.string().nullish(),
  base_date: z.string().nullish(),
  latest_date: z.string().nullish(),
  data_date: z.string().nullish(),
  price_as_of: z.string().nullish(),
  as_of: z.string().nullish(),
}) satisfies z.ZodType<DataBasisItem>

const reportRecordItemShape = {
  report_id: z.number(),
  previous_report_id: z.number().nullable(),
  question: z.string(),
  summary: z.string().nullable(),
  company_name: z.string().nullable(),
  created_at: z.string(),
  used_tools: z.array(z.string()),
  title: z.string(),
  company_names: z.array(z.string()),
  tickers: z.array(z.string()),
  analysis_type: analysisTypeSchema,
  analysis_types: z.array(analysisTypeSchema),
  source_count: z.number(),
  local_date: z.string(),
  local_time: z.string(),
  date_group: dateGroupSchema,
  locale: z.string(),
}

export const reportRecordListSchema = z.object({
  ...pageInfoShape,
  timezone: z.string(),
  items: z.array(z.object(reportRecordItemShape)),
}) satisfies z.ZodType<ReportRecordList>

export const reportRecordDetailSchema = z.object({
  ...reportRecordItemShape,
  answer: z.string(),
  sources: z.array(sourceSchema),
  disclaimer: z.string(),
  data_basis: z.array(dataBasisItemSchema),
  timezone: z.string(),
  translation_status: translationStatusSchema.nullable(),
  original_answer: z.string().nullable(),
}) satisfies z.ZodType<ReportRecordDetail>

// 기준일 한 줄 문구(없으면 null). tool마다 남는 날짜 필드가 달라서, 가장 "그 데이터의 날짜"에 가까운 것부터 고른다.
// 기간형(period_start~period_end)이면 범위로, 아니면 하나의 기준일로 보여준다.
export function describeDataBasis(item: DataBasisItem): string | null {
  if (item.period_start_date && item.period_end_date) return `${item.period_start_date} ~ ${item.period_end_date}`
  const single =
    item.price_as_of ?? item.latest_date ?? item.data_date ?? item.base_date ?? item.period_end_date ?? item.as_of
  return single ?? null
}
