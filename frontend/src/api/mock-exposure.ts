import type { EtfHoldings } from './contracts/etf'
import type { ExposureParams, LookThroughView } from './contracts/exposure'
import type { EtfApi } from './domains/etf'
import type { ExposureApi } from './domains/exposure'
import { ApiError } from './errors'
import { currentUserId, storeFor } from './mock'
import {
  collectedSeries,
  delay,
  expectedTradingDate,
  findCompanyMeta,
  mockDataLagDays,
} from './mock-core'
import { computeLookThrough, mockEtfDefinition } from './mock-exposure-core'
import { toLocale, translate } from '../i18n'

// GET /api/portfolio/exposure?view=look_through. 계산은 mock-exposure-core(백엔드 공식과 같은 규칙).
async function getLookThroughExposure(params: ExposureParams = {}): Promise<LookThroughView> {
  await delay(250)
  const store = storeFor(currentUserId())
  const limit = Math.min(50, Math.max(1, params.limit ?? 10))
  const dates = collectedSeries('005930').map((p) => p.price_date)
  return computeLookThrough({
    cash: store.cashBalance,
    holdings: [...store.holdings].map(([ticker, h]) => ({ ticker, quantity: h.quantity })),
    // "수집된" 시세(지연 훅이 걸려 있으면 마지막 N일 빠짐)로 평가한다
    priceOf: (ticker) => {
      const last = collectedSeries(ticker).at(-1)
      return last ? { price: last.close_price, date: last.price_date } : null
    },
    limit,
    locale: toLocale(params.locale ?? store.preferences.locale),
    expectedDate: expectedTradingDate(),
    lagBusinessDays: mockDataLagDays(),
    tradingDates: dates,
  })
}

// GET /api/etf/{ticker}/holdings (공개). KODEX·TIGER 일부만 수집하고 나머지는 "수집하지 않았습니다"(200, items: []).
async function getEtfHoldings(ticker: string, limit = 10): Promise<EtfHoldings> {
  await delay(200)
  if (!Number.isInteger(limit) || limit < 1 || limit > 50) {
    throw new ApiError('limit은 1~50 사이의 정수여야 해요.', 422)
  }
  const meta = findCompanyMeta(ticker)
  if (!meta) {
    throw new ApiError(`'${ticker}' 종목을 찾지 못했습니다. company 테이블에 등록된 종목명 또는 종목코드를 지정해주세요.`, 404)
  }
  const disclaimer = translate('ko-KR', 'system.disclaimer')
  const def = meta.assetType === 'etf' ? mockEtfDefinition(meta.ticker) : undefined
  if (!def) {
    return {
      ticker: meta.ticker,
      name: meta.name,
      asset_type: meta.assetType,
      as_of_date: null,
      collected_at: null,
      stale: false,
      stale_notice: null,
      source: null,
      holding_count: 0,
      total_weight_pct: null,
      top_weight_pct: null,
      limit,
      items: [],
      data_warnings: [],
      message:
        meta.assetType === 'etf'
          ? `${meta.name}의 구성종목은 수집하지 않았습니다.`
          : `${meta.name}은(는) ETF가 아니라 구성종목이 없습니다.`,
      disclaimer,
    }
  }

  const dates = collectedSeries('005930').map((p) => p.price_date)
  const asOf = dates[Math.max(0, dates.length - 1 - def.holdingsLagDays)] ?? null
  const sorted = [...def.constituents].sort((a, b) => b.weightPct - a.weightPct)
  const items = sorted.slice(0, limit).map((c, i) => ({ rank: i + 1, name: c.name, code: c.code, weight_pct: c.weightPct }))
  const total = Math.round(def.constituents.reduce((s, c) => s + c.weightPct, 0) * 100) / 100
  const stale = def.holdingsLagDays >= 2
  return {
    ticker: meta.ticker,
    name: meta.name,
    asset_type: meta.assetType,
    as_of_date: asOf,
    collected_at: new Date().toISOString(),
    stale,
    stale_notice: stale ? `구성종목 기준일(${asOf})이 최근 영업일보다 ${def.holdingsLagDays}영업일 뒤처졌습니다.` : null,
    source: '샘플 데이터(운용사 공시 PDF를 흉내 낸 값)',
    holding_count: def.constituentCount,
    total_weight_pct: total,
    top_weight_pct: Math.round(items.reduce((s, c) => s + c.weight_pct, 0) * 100) / 100,
    limit,
    items,
    data_warnings: [],
    message: null,
    disclaimer,
  }
}

export const exposureMock: ExposureApi = { getLookThroughExposure }
export const etfMock: EtfApi = { getEtfHoldings }
