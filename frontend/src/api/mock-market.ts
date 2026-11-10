import type { BreadthCounts, MarketBreadth, MarketIndexOverview, MarketOverview } from './contracts/market'
import type { MarketApi } from './domains/market'
import {
  COMPANY_META,
  collectedSeries,
  delay,
  expectedTradingDate,
  getPriceSeries,
  mockDataLagDays,
  syntheticIndexCloses,
} from './mock-core'

// GET /api/market/overview mock. 지수는 브리핑 mock과 같은 시드를 써서 두 화면의 숫자가 어긋나지 않는다.
// 상승·하락 수는 mock 종목(주식만)의 마지막 두 종가로 센다 - 백엔드처럼 "수집 대상 종목" 기준이다.
const round1 = (n: number) => Math.round(n * 10) / 10
const round2 = (n: number) => Math.round(n * 100) / 100

const INDEX_DEFS = [
  { code: 'KS11', name: '코스피', base: 2700, seed: 11 },
  { code: 'KQ11', name: '코스닥', base: 850, seed: 22 },
] as const

function freshnessStatus(lag: number): 'latest' | 'delayed' | 'stale' {
  if (lag <= 0) return 'latest'
  return lag <= 5 ? 'delayed' : 'stale'
}

function countsOf(changes: (number | null)[], registered: number): BreadthCounts {
  const known = changes.filter((c): c is number => c != null)
  const up = known.filter((c) => c > 0).length
  const down = known.filter((c) => c < 0).length
  const flat = known.length - up - down
  const denominator = up + down + flat
  const ratio = (n: number) => (denominator > 0 ? round1((n / denominator) * 100) : null)
  return {
    total: changes.length,
    up,
    down,
    flat,
    change_missing: changes.length - known.length,
    no_price: registered - changes.length,
    registered,
    up_ratio_pct: ratio(up),
    down_ratio_pct: ratio(down),
    flat_ratio_pct: ratio(flat),
  }
}

function buildBreadth(date: string): MarketBreadth | null {
  const stocks = COMPANY_META.filter((c) => c.assetType === 'stock')
  const changes = new Map<string, number | null>()
  for (const meta of stocks) {
    const series = collectedSeries(meta.ticker)
    const last = series.at(-1)
    const prev = series.at(-2)
    changes.set(meta.ticker, last && prev ? round2((last.close_price / prev.close_price - 1) * 100) : null)
  }
  const all = countsOf([...changes.values()], stocks.length)
  const byMarket: Record<string, BreadthCounts> = {}
  for (const market of ['KOSPI', 'KOSDAQ'] as const) {
    const inMarket = stocks.filter((c) => c.market === market)
    byMarket[market] = countsOf(inMarket.map((c) => changes.get(c.ticker) ?? null), inMarket.length)
  }
  return { ...all, date, universe: 'collected_stocks', by_market: byMarket }
}

async function getMarketOverview(): Promise<MarketOverview> {
  await delay(120)
  const lag = mockDataLagDays()
  const dates = collectedSeries('005930').map((p) => p.price_date)
  const length = getPriceSeries('005930').length
  const lastDate = dates.at(-1) ?? null

  const indices: MarketIndexOverview[] = INDEX_DEFS.map((def) => {
    const closes = syntheticIndexCloses(def.base, def.seed, length, 0.03).slice(0, dates.length)
    const close = closes.at(-1) ?? null
    const previous = closes.at(-2)
    const change = close != null && previous != null ? round2(close - previous) : null
    return {
      code: def.code,
      name: def.name,
      date: lastDate,
      close,
      previous_date: dates.at(-2) ?? null,
      change,
      change_pct: close != null && previous != null ? round2((close / previous - 1) * 100) : null,
      change_unavailable_reason: change == null ? 'no_previous_row' : null,
      lag_business_days: lag,
    }
  })

  return {
    as_of: lastDate,
    expected_date: expectedTradingDate(),
    status: 'ok',
    missing: [],
    freshness: { status: freshnessStatus(lag), lag_business_days: lag, intraday_excluded: false, dates_differ: false },
    indices,
    breadth: lastDate ? buildBreadth(lastDate) : null,
    last_collection: {
      index: { status: 'success', finished_at: null },
      prices: { status: 'success', finished_at: null },
    },
    unavailable: ['trading_value', 'market_volume'],
    note: '저장된 일별 종가 기준이며 실시간 시세가 아닙니다. 상승·하락 종목 수는 수집 대상 종목만 센 값입니다.',
  }
}

export const marketMock: MarketApi = { getMarketOverview }
