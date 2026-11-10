import { z } from 'zod'

// GET /api/stocks/{ticker}/news, /disclosures (공개, backend/API.md 2026-11-10 41차). 저장된 기사·공시를 페이지로 나눠 줄 뿐
// 새로 요약하거나 계산하지 않는다. 종목 해석은 엄격 모드라 URL의 티커로 부르면 되고, 오타 이름은 404다.
export type FeedOrder = 'desc' | 'asc'
export type CorrectionFilter = 'all' | 'only' | 'exclude'

export interface FeedParams {
  limit?: number
  offset?: number
  // KST 달력 날짜(YYYY-MM-DD), 양끝 포함. date_from > date_to면 서버가 422다(보내기 전에 막는다).
  date_from?: string
  date_to?: string
  order?: FeedOrder
}

export interface DisclosureParams extends FeedParams {
  correction?: CorrectionFilter
}

export interface FeedCollectionAttempt {
  step: string
  // success | partial | failed. 모르는 값은 그대로 두고 failed만 따로 본다.
  status: string
  finished_at: string | null
  error_code: string | null
}

export interface FeedFreshness {
  // 뉴스(공시) 전체의 마지막 저장 시각(KST). 이 종목만의 값이 아니다.
  last_collected_at: string | null
  collection_lag_weekdays: number | null
  // 전체 수집이 2평일 이상 멈췄다
  stale: boolean
  // 이 종목의 가장 최근 항목 시각
  company_latest_at: string | null
  last_collection: FeedCollectionAttempt | null
}

interface FeedPage {
  ticker: string
  company_name: string
  asset_type: 'stock' | 'etf'
  total: number
  limit: number
  offset: number
  has_next: boolean
  total_pages: number
  freshness: FeedFreshness
}

export interface StockNewsItem {
  id: number
  title: string
  // 네이버 검색 API가 준 기사 앞부분 발췌다. "AI 요약"이라고 표시하지 않는다. null이면 숨긴다.
  summary: string | null
  source: string | null
  url: string | null
  // KST ISO 8601
  published_at: string | null
  collected_at: string | null
  is_relevant: boolean | null
  // 같은 기사가 연결된 종목들. 이 종목이 들어 있을 수도, 없을 수도 있고 비어 있을 수도 있다.
  related_tickers: string[]
}

export interface StockNews extends FeedPage {
  items: StockNewsItem[]
}

export interface StockDisclosureItem {
  id: number
  title: string
  disclosure_type: string | null
  rcept_no: string | null
  // 접수일(YYYY-MM-DD). 공시는 시각을 저장하지 않아 날짜만 보여준다(disclosed_at은 T00:00:00+09:00라 시각으로 쓰지 않는다).
  disclosed_date: string | null
  disclosed_at: string | null
  source_url: string | null
  // [기재정정] 등. 원본과 접수번호가 다른 별개 공시라 둘 다 목록에 나온다.
  is_correction: boolean
  collected_at: string | null
}

export interface StockDisclosures extends FeedPage {
  // ETF는 DART 공시 수집 대상이 아니라 false(200, items: [], message)다
  supported: boolean
  message: string | null
  items: StockDisclosureItem[]
}

const attemptSchema = z.object({
  step: z.string(),
  status: z.string(),
  finished_at: z.string().nullable(),
  error_code: z.string().nullable(),
}) satisfies z.ZodType<FeedCollectionAttempt>

const freshnessSchema = z.object({
  last_collected_at: z.string().nullable(),
  collection_lag_weekdays: z.number().nullable(),
  stale: z.boolean(),
  company_latest_at: z.string().nullable(),
  last_collection: attemptSchema.nullable(),
}) satisfies z.ZodType<FeedFreshness>

const pageShape = {
  ticker: z.string(),
  company_name: z.string(),
  asset_type: z.enum(['stock', 'etf']),
  total: z.number(),
  limit: z.number(),
  offset: z.number(),
  has_next: z.boolean(),
  total_pages: z.number(),
  freshness: freshnessSchema,
}

export const stockNewsSchema = z.object({
  ...pageShape,
  items: z.array(
    z.object({
      id: z.number(),
      title: z.string(),
      summary: z.string().nullable(),
      source: z.string().nullable(),
      url: z.string().nullable(),
      published_at: z.string().nullable(),
      collected_at: z.string().nullable(),
      is_relevant: z.boolean().nullable(),
      related_tickers: z.array(z.string()),
    }) satisfies z.ZodType<StockNewsItem>,
  ),
}) satisfies z.ZodType<StockNews>

export const stockDisclosuresSchema = z.object({
  ...pageShape,
  supported: z.boolean(),
  message: z.string().nullable(),
  items: z.array(
    z.object({
      id: z.number(),
      title: z.string(),
      disclosure_type: z.string().nullable(),
      rcept_no: z.string().nullable(),
      disclosed_date: z.string().nullable(),
      disclosed_at: z.string().nullable(),
      source_url: z.string().nullable(),
      is_correction: z.boolean(),
      collected_at: z.string().nullable(),
    }) satisfies z.ZodType<StockDisclosureItem>,
  ),
}) satisfies z.ZodType<StockDisclosures>

// 목록이 비었을 때 이유. 마지막 수집 시도가 실패했으면 "수집 실패", 아니면 "그 기간에 저장된 게 없다"(핸드오프 41차 3절).
export function feedEmptyReason(freshness: FeedFreshness): 'collection_failed' | 'none' {
  return freshness.last_collection?.status === 'failed' ? 'collection_failed' : 'none'
}

// date_from > date_to는 서버가 422로 막는다. 보내기 전에 걸러서 오류 화면 대신 입력 옆 안내로 보여준다.
export function dateRangeInvalid(from: string, to: string): boolean {
  return from !== '' && to !== '' && from > to
}

// 이 기사가 연결된 다른 종목(현재 종목 제외, 중복 제거)
export function otherTickers(item: StockNewsItem, current: string): string[] {
  return [...new Set(item.related_tickers.filter((t) => t !== current))]
}
