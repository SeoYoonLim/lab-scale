import { z } from 'zod'

// GET /api/etf/{ticker}/holdings (공개, backend/API.md 2026-10-30). 구성종목·편입 비중은 운용사가 공시한 PDF 값이다.
// KODEX·TIGER 38개만 수집하고(나머지 ETF는 "수집하지 않았습니다"), 데이터가 없어도 200 + items: []다.
export interface EtfHoldingItem {
  rank: number
  name: string
  // 해외 종목은 'NVDA US EQUITY' 같은 운용사 표기, 없으면 null
  code: string | null
  weight_pct: number
}

export interface EtfHoldings {
  ticker: string
  name: string
  asset_type: string
  // 운용사가 공시한 기준일. 데이터가 없으면 null
  as_of_date: string | null
  collected_at: string | null
  // 기준일이 최근 영업일보다 2영업일 이상 뒤처졌는가
  stale: boolean
  stale_notice: string | null
  source: string | null
  holding_count: number
  // 저장된 전체 구성종목의 비중 합계(limit과 무관). 비중 합계가 100%에서 조금 벗어나는 건 정상이다.
  total_weight_pct: number | null
  // items(상위 limit개)의 비중 합계
  top_weight_pct: number | null
  limit: number
  items: EtfHoldingItem[]
  data_warnings: { kind: string; message: string }[]
  // 데이터가 없을 때만 사유(items=[])
  message: string | null
  disclaimer: string
}

export const etfHoldingsSchema = z.object({
  ticker: z.string(),
  name: z.string(),
  asset_type: z.string(),
  as_of_date: z.string().nullable(),
  collected_at: z.string().nullable(),
  stale: z.boolean(),
  stale_notice: z.string().nullable(),
  source: z.string().nullable(),
  holding_count: z.number(),
  total_weight_pct: z.number().nullable(),
  top_weight_pct: z.number().nullable(),
  limit: z.number(),
  items: z.array(
    z.object({ rank: z.number(), name: z.string(), code: z.string().nullable(), weight_pct: z.number() }),
  ),
  data_warnings: z.array(z.object({ kind: z.string(), message: z.string() })),
  message: z.string().nullable(),
  disclaimer: z.string(),
}) satisfies z.ZodType<EtfHoldings>
