import type {
  DisclosureParams,
  FeedFreshness,
  FeedParams,
  StockDisclosureItem,
  StockDisclosures,
  StockNews,
  StockNewsItem,
} from './contracts/stockFeed'
import type { StockFeedApi } from './domains/stockFeed'
import { ApiError } from './errors'
import {
  COMPANY_META,
  DISCLOSURE_TYPES,
  companyNotFound,
  delay,
  expectedTradingDate,
  findCompanyMeta,
  mockDataLagDays,
  mulberry32,
} from './mock-core'

// GET /api/stocks/{ticker}/news, /disclosures mock. 실제 서버처럼 페이지·날짜 범위·정렬을 지키고, ETF 공시는 200 + supported=false다.
// 기사·공시 내용은 전부 샘플이다(제목에 [샘플]이 붙는다).
const ETF_DISCLOSURE_MESSAGE =
  'ETF는 DART 공시 수집 대상이 아니어서 공시 목록이 없습니다. 뉴스 목록(GET /api/stocks/{ticker}/news)을 이용하세요.'

function shiftDate(date: string, days: number): string {
  const d = new Date(`${date}T00:00:00Z`)
  d.setUTCDate(d.getUTCDate() - days)
  return d.toISOString().slice(0, 10)
}

function seedOf(ticker: string): number {
  return (Number(ticker) || 1) * 31 + 5
}

function newsFor(ticker: string, name: string, isEtf: boolean): StockNewsItem[] {
  const rand = mulberry32(seedOf(ticker))
  const latest = expectedTradingDate()
  const others = COMPANY_META.filter((c) => c.ticker !== ticker)
  const count = isEtf ? 24 : 47
  return Array.from({ length: count }, (_, i) => {
    const date = shiftDate(latest, Math.floor(i * 1.2))
    const hh = String(9 + Math.floor(rand() * 9)).padStart(2, '0')
    const mm = String(Math.floor(rand() * 60)).padStart(2, '0')
    const related = i % 4 === 1 ? [ticker, others[i % others.length].ticker] : [ticker]
    return {
      id: 10_000 + i,
      title: `[샘플] ${name} 관련 뉴스 ${i + 1}`,
      // 실제로는 네이버 검색 API가 준 기사 앞부분 발췌다
      summary: i % 5 === 3 ? null : `[샘플] ${name} 관련 기사 앞부분 발췌입니다. 실제 서비스에서는 네이버 검색 API가 준 문장이 들어옵니다.`,
      source: 'sample.news',
      url: `https://search.naver.com/search.naver?where=news&query=${encodeURIComponent(name)}`,
      published_at: `${date}T${hh}:${mm}:00+09:00`,
      collected_at: `${date}T19:50:00+09:00`,
      is_relevant: true,
      related_tickers: related,
    }
  })
}

function disclosuresFor(name: string): StockDisclosureItem[] {
  const latest = expectedTradingDate()
  return Array.from({ length: 14 }, (_, i) => {
    const date = shiftDate(latest, i * 3)
    const type = DISCLOSURE_TYPES[i % DISCLOSURE_TYPES.length]
    const correction = i % 5 === 2
    const title = `${correction ? '[기재정정]' : ''}${type}`
    const rcept = `${date.replaceAll('-', '')}8${String(1000 + i).padStart(5, '0')}`
    return {
      id: 20_000 + i,
      title: `[샘플] ${name} ${title}`,
      disclosure_type: title,
      rcept_no: rcept,
      disclosed_date: date,
      disclosed_at: `${date}T00:00:00+09:00`,
      source_url: `https://dart.fss.or.kr/dsaf001/main.do?rcptNo=${rcept}`,
      is_correction: correction,
      collected_at: `${date}T19:50:00+09:00`,
    }
  })
}

function freshness(newestAt: string | null): FeedFreshness {
  const lag = mockDataLagDays()
  const collected = `${shiftDate(expectedTradingDate(), lag)}T19:50:00+09:00`
  return {
    last_collected_at: collected,
    collection_lag_weekdays: lag,
    // 실제 서버: 전체 수집이 2평일 이상 멈췄을 때 stale
    stale: lag >= 2,
    company_latest_at: newestAt,
    last_collection: { step: 'news', status: 'success', finished_at: collected, error_code: null },
  }
}

function validate(params: FeedParams): { limit: number; offset: number } {
  const limit = params.limit ?? 20
  const offset = params.offset ?? 0
  if (!Number.isInteger(limit) || limit < 1 || limit > 50) throw new ApiError('limit은 1~50 사이의 정수여야 해요.', 422)
  if (!Number.isInteger(offset) || offset < 0) throw new ApiError('offset은 0 이상의 정수여야 해요.', 422)
  if (params.date_from && params.date_to && params.date_from > params.date_to) {
    throw new ApiError('date_from이 date_to보다 늦습니다.', 422)
  }
  return { limit, offset }
}

function page<T>(items: T[], limit: number, offset: number) {
  return {
    total: items.length,
    limit,
    offset,
    has_next: offset + limit < items.length,
    total_pages: items.length === 0 ? 0 : Math.ceil(items.length / limit),
    items: items.slice(offset, offset + limit),
  }
}

function sortByDate<T>(items: T[], dateOf: (item: T) => string, order: 'asc' | 'desc'): T[] {
  const sorted = [...items].sort((a, b) => dateOf(b).localeCompare(dateOf(a)))
  return order === 'asc' ? sorted.reverse() : sorted
}

async function getStockNews(ticker: string, params: FeedParams = {}): Promise<StockNews> {
  await delay(250)
  const meta = findCompanyMeta(ticker)
  if (!meta) throw companyNotFound(ticker)
  const { limit, offset } = validate(params)
  const all = newsFor(meta.ticker, meta.name, meta.assetType === 'etf').filter((n) => {
    const day = (n.published_at ?? '').slice(0, 10)
    return (!params.date_from || day >= params.date_from) && (!params.date_to || day <= params.date_to)
  })
  const sorted = sortByDate(all, (n) => n.published_at ?? '', params.order ?? 'desc')
  return {
    ticker: meta.ticker,
    company_name: meta.name,
    asset_type: meta.assetType,
    ...page(sorted, limit, offset),
    freshness: freshness(sorted.length > 0 ? (sortByDate(all, (n) => n.published_at ?? '', 'desc')[0].published_at ?? null) : null),
  }
}

async function getStockDisclosures(ticker: string, params: DisclosureParams = {}): Promise<StockDisclosures> {
  await delay(250)
  const meta = findCompanyMeta(ticker)
  if (!meta) throw companyNotFound(ticker)
  const { limit, offset } = validate(params)
  const base = { ticker: meta.ticker, company_name: meta.name, asset_type: meta.assetType }
  if (meta.assetType === 'etf') {
    return { ...base, ...page([], limit, offset), supported: false, message: ETF_DISCLOSURE_MESSAGE.replace('{ticker}', meta.ticker), freshness: freshness(null) }
  }
  const all = disclosuresFor(meta.name).filter((d) => {
    const day = d.disclosed_date ?? ''
    const inRange = (!params.date_from || day >= params.date_from) && (!params.date_to || day <= params.date_to)
    const correction = params.correction ?? 'all'
    return inRange && (correction === 'all' || (correction === 'only') === d.is_correction)
  })
  const sorted = sortByDate(all, (d) => d.disclosed_date ?? '', params.order ?? 'desc')
  return {
    ...base,
    ...page(sorted, limit, offset),
    supported: true,
    message: null,
    freshness: freshness(sorted.length > 0 ? (sortByDate(all, (d) => d.disclosed_date ?? '', 'desc')[0].disclosed_at ?? null) : null),
  }
}

export const stockFeedMock: StockFeedApi = { getStockNews, getStockDisclosures }
