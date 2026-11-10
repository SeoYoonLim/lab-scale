import { z } from 'zod'
import type { MarketDataStatus } from './common'

// GET /api/market/overview (공개, backend/API.md 2026-11-10 41차). 일일 브리핑 없이 코스피·코스닥 종가와 상승·하락 종목 수를 읽는다.
// 데이터가 없어도 200이고 status로 구분한다. 거래대금·시장 거래량은 저장하지 않아 없다(unavailable).
export type MarketOverviewStatus = 'ok' | 'partial' | 'no_data'

export interface MarketIndexOverview {
  // KS11(코스피)·KQ11(코스닥). 이름(name)은 한국어라 다른 언어 화면은 code로 라벨을 고른다.
  code: string
  name: string
  date: string | null
  close: number | null
  previous_date: string | null
  // 직전 저장 행이 직전 거래일일 때만 계산된다. 아니면 null이고 이유가 온다 - 0%로 그리지 않는다.
  change: number | null
  change_pct: number | null
  change_unavailable_reason: string | null
  lag_business_days: number | null
}

export interface BreadthCounts {
  total: number
  up: number
  down: number
  flat: number
  change_missing: number
  no_price: number
  registered: number
  // 분모는 up + down + flat이다(등락률 결측 제외). 분모가 0이면 null.
  up_ratio_pct: number | null
  down_ratio_pct: number | null
  flat_ratio_pct: number | null
}

export interface MarketBreadth extends BreadthCounts {
  date: string
  // 시장 전체가 아니라 수집 대상(시가총액 상위) 주식만 센 값이다
  universe: string
  by_market: Record<string, BreadthCounts>
}

export interface MarketCollectionStep {
  status: string
  finished_at: string | null
}

export interface MarketOverview {
  as_of: string | null
  expected_date: string
  status: MarketOverviewStatus
  missing: string[]
  freshness: {
    status: MarketDataStatus
    lag_business_days: number | null
    intraday_excluded: boolean
    dates_differ: boolean
  }
  indices: MarketIndexOverview[]
  breadth: MarketBreadth | null
  last_collection: {
    index?: MarketCollectionStep | null
    prices?: MarketCollectionStep | null
  }
  unavailable: string[]
  note: string
}

const indexSchema = z.object({
  code: z.string(),
  name: z.string(),
  date: z.string().nullable(),
  close: z.number().nullable(),
  previous_date: z.string().nullable(),
  change: z.number().nullable(),
  change_pct: z.number().nullable(),
  change_unavailable_reason: z.string().nullable(),
  lag_business_days: z.number().nullable(),
}) satisfies z.ZodType<MarketIndexOverview>

const breadthCountsSchema = z.object({
  total: z.number(),
  up: z.number(),
  down: z.number(),
  flat: z.number(),
  change_missing: z.number(),
  no_price: z.number(),
  registered: z.number(),
  up_ratio_pct: z.number().nullable(),
  down_ratio_pct: z.number().nullable(),
  flat_ratio_pct: z.number().nullable(),
}) satisfies z.ZodType<BreadthCounts>

const collectionStepSchema = z.object({
  status: z.string(),
  finished_at: z.string().nullable(),
}) satisfies z.ZodType<MarketCollectionStep>

export const marketOverviewSchema = z.object({
  as_of: z.string().nullable(),
  expected_date: z.string(),
  status: z.enum(['ok', 'partial', 'no_data']),
  missing: z.array(z.string()),
  freshness: z.object({
    status: z.enum(['latest', 'delayed', 'stale', 'no_data']),
    lag_business_days: z.number().nullable(),
    intraday_excluded: z.boolean(),
    dates_differ: z.boolean(),
  }),
  indices: z.array(indexSchema),
  breadth: breadthCountsSchema
    .extend({ date: z.string(), universe: z.string(), by_market: z.record(z.string(), breadthCountsSchema) })
    .nullable(),
  last_collection: z.object({
    index: collectionStepSchema.nullish(),
    prices: collectionStepSchema.nullish(),
  }),
  unavailable: z.array(z.string()),
  note: z.string(),
}) satisfies z.ZodType<MarketOverview>

// 지수 라벨은 이름(한국어)이 아니라 code로 고른다. 모르는 code는 서버 이름을 그대로 쓴다.
export function indexLabelKey(code: string): { key: string; fallback: string } | null {
  if (code === 'KS11') return { key: 'market.kospi', fallback: '코스피' }
  if (code === 'KQ11') return { key: 'market.kosdaq', fallback: '코스닥' }
  return null
}

// 등락이 비어 있는 이유(백엔드가 준 코드)를 사람이 읽는 말로 바꾼다. 모르는 코드는 null(이유 없이 "—"만).
export function changeUnavailableKey(reason: string | null): { key: string; fallback: string } | null {
  if (reason === 'no_previous_row') return { key: 'market.change_no_previous', fallback: '비교할 이전 종가가 없어요' }
  if (reason === 'previous_trading_day_missing') {
    return { key: 'market.change_gap', fallback: '직전 거래일 종가가 수집되지 않아 등락을 계산하지 않았어요' }
  }
  return null
}

// 상승·하락·보합 막대의 비율. 서버가 준 비율(소수 첫째 자리)을 그대로 쓰고, 합이 100을 넘지 않게 막대 폭만 맞춘다.
// 분모가 0이라 비율이 없으면 null(막대를 그리지 않는다).
export function breadthBarSegments(b: BreadthCounts): { key: 'up' | 'down' | 'flat'; pct: number; count: number }[] | null {
  if (b.up_ratio_pct == null || b.down_ratio_pct == null || b.flat_ratio_pct == null) return null
  return [
    { key: 'up', pct: b.up_ratio_pct, count: b.up },
    { key: 'flat', pct: b.flat_ratio_pct, count: b.flat },
    { key: 'down', pct: b.down_ratio_pct, count: b.down },
  ]
}
