import type {
  Company,
  DisclosureItem,
  Holding,
  NewsItem,
  OrderResult,
  Portfolio,
  PricePoint,
  RealtimePrice,
  ReportDetail,
  ReportListItem,
  ResearchResponse,
  Source,
  TrendingCategory,
  TrendingItem,
  TrendingResponse,
  WatchlistItem,
} from '../types'
import type { Api } from './index'

// 화면 개발용 샘플 데이터. 실제 시세/뉴스/공시가 아니다.

const COMPANIES: Company[] = [
  { ticker: '005930', name: '삼성전자', market: 'KOSPI', sector: '전기전자' },
  { ticker: '000660', name: 'SK하이닉스', market: 'KOSPI', sector: '전기전자' },
  { ticker: '035420', name: 'NAVER', market: 'KOSPI', sector: '서비스업' },
]

const BASE_PRICE: Record<string, number> = {
  '005930': 70000,
  '000660': 180000,
  '035420': 200000,
}

const DISCLOSURE_TYPES = [
  '분기보고서',
  '주요사항보고서(자기주식취득결정)',
  '주식등의대량보유상황보고서',
  '기업설명회(IR)개최',
  '영업(잠정)실적(공정공시)',
]

const delay = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms))

function notFound(reportId: number): Error {
  return new Error(`report_id=${reportId} 리포트를 찾을 수 없습니다.`)
}

function companyNotFound(input: string): Error {
  return new Error(`'${input}' 종목을 찾지 못했습니다. company 테이블에 등록된 종목명 또는 종목코드를 지정해주세요.`)
}

// 같은 종목은 항상 같은 값이 나오도록 시드 기반 난수를 쓴다.
function mulberry32(seed: number) {
  let a = seed
  return () => {
    a |= 0
    a = (a + 0x6d2b79f5) | 0
    let t = Math.imul(a ^ (a >>> 15), 1 | a)
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

function toDateString(d: Date) {
  const y = d.getFullYear()
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  return `${y}-${m}-${day}`
}

const priceCache = new Map<string, PricePoint[]>()

// 오늘까지 90거래일(주말 제외) 치 주가를 만든다.
function getPriceSeries(ticker: string): PricePoint[] {
  const cached = priceCache.get(ticker)
  if (cached) return cached

  const rand = mulberry32(Number(ticker) || 1)
  const dates: string[] = []
  const cursor = new Date()
  while (dates.length < 90) {
    const weekday = cursor.getDay()
    if (weekday !== 0 && weekday !== 6) dates.push(toDateString(cursor))
    cursor.setDate(cursor.getDate() - 1)
  }
  dates.reverse()

  let prevClose = BASE_PRICE[ticker] ?? 50000
  const series = dates.map((price_date) => {
    const move = (rand() - 0.5) * 0.05
    const close = Math.max(100, Math.round((prevClose * (1 + move)) / 100) * 100)
    const change_pct = Math.round(((close - prevClose) / prevClose) * 10000) / 100
    const volume = Math.round(8_000_000 + rand() * 20_000_000)
    prevClose = close
    return { price_date, close_price: close, volume, change_pct }
  })

  priceCache.set(ticker, series)
  return series
}

function findCompany(ticker: string) {
  return COMPANIES.find((c) => c.ticker === ticker || c.name === ticker)
}

function makeNews(name: string, limit: number): NewsItem[] {
  return Array.from({ length: limit }, (_, i) => {
    const published = new Date()
    published.setHours(published.getHours() - (i * 7 + 2))
    return {
      title: `[샘플] ${name} 관련 뉴스 ${i + 1}`,
      source: 'sample.example.com',
      published_at: published.toISOString(),
      url: `https://search.naver.com/search.naver?where=news&query=${encodeURIComponent(name)}`,
      content: `${name} 관련 샘플 기사 요약입니다. 실제 뉴스가 아닙니다.`,
    }
  })
}

function makeDisclosures(name: string, limit: number): DisclosureItem[] {
  return Array.from({ length: limit }, (_, i) => {
    const disclosed = new Date()
    disclosed.setDate(disclosed.getDate() - (i * 9 + 3))
    const type = DISCLOSURE_TYPES[i % DISCLOSURE_TYPES.length]
    return {
      title: `[샘플] ${name} ${type}`,
      disclosure_type: type,
      disclosed_at: disclosed.toISOString(),
      source_url: 'https://dart.fss.or.kr',
    }
  })
}

// ---------- 리서치 리포트 (실제 /api/research 계약을 흉내) ----------

const reportStore: ReportDetail[] = []
let nextReportId = 1

function toListItem(report: ReportDetail): ReportListItem {
  const { report_id, previous_report_id, question, summary, company_name, created_at, used_tools } = report
  return { report_id, previous_report_id, question, summary, company_name, created_at, used_tools }
}

function summarize(answer: string): string {
  const flat = answer.replace(/\s+/g, ' ').trim()
  return flat.length > 200 ? `${flat.slice(0, 200)}...` : flat
}

// 백엔드 에이전트(backend/app/agent.py)의 도구 선택 규칙을 흉내 낸다.
async function askResearch(question: string, previousReportId?: number | null): Promise<ResearchResponse> {
  await delay(1200)

  let previous: ReportDetail | undefined
  if (previousReportId != null) {
    previous = reportStore.find((r) => r.report_id === previousReportId)
    if (!previous) throw notFound(previousReportId)
  }

  const company =
    COMPANIES.find((c) => question.includes(c.name) || question.includes(c.ticker)) ??
    (previous ? findCompany(previous.company_name ?? '') : undefined)

  const wantsDisclosure = /공시|사업보고서|자사주|공식/.test(question)
  const wantsNews = /뉴스|이슈|왜|소식/.test(question)
  const wantsStock = /주가|등락|거래량|오른|올랐|내렸|내린|하락|상승|시세/.test(question)
  const useStock = company != null && (wantsStock || (!wantsNews && !wantsDisclosure))

  const used_tools: string[] = []
  const sources: Source[] = []
  const summaryParts: string[] = []

  if (useStock && company) {
    const prices = getPriceSeries(company.ticker).slice(-1)
    const latest = prices[0]
    used_tools.push('stock_tool')
    summaryParts.push(
      `최근 등락률은 ${latest.change_pct}%, 거래량은 ${latest.volume?.toLocaleString('ko-KR')}주예요.`,
    )
  }
  if (wantsNews && company) {
    used_tools.push('news_tool')
    for (const item of makeNews(company.name, 3)) {
      sources.push({
        tool: 'news_tool',
        type: 'news',
        title: item.title,
        company_names: [company.name],
        company_filter: null,
        url: item.url,
      })
    }
    summaryParts.push('관련 뉴스를 확인했어요.')
  }
  if (wantsDisclosure && company) {
    used_tools.push('disclosure_tool')
    for (const item of makeDisclosures(company.name, 3)) {
      sources.push({
        tool: 'disclosure_tool',
        type: 'disclosure',
        title: item.title,
        company_names: [company.name],
        company_filter: null,
        url: item.source_url,
      })
    }
    summaryParts.push('최근 공시를 확인했어요.')
  }

  const intro = previous ? `[샘플 응답 · "${previous.question}"에 이어서] ` : '[샘플 응답] '
  const answer = company
    ? `${intro}${company.name}에 대해 조회했어요. ${summaryParts.join(' ')}\n실제 백엔드가 연결되면 llama3.1:8b가 도구 결과를 바탕으로 만든 답변이 여기에 표시돼요.`
    : `${intro}도구를 호출하지 않고 바로 답하는 경우예요. 종목명(삼성전자, SK하이닉스, NAVER)을 넣어서 질문하면 근거(sources)도 함께 볼 수 있어요.`

  const report: ReportDetail = {
    report_id: nextReportId++,
    previous_report_id: previousReportId ?? null,
    question,
    answer,
    summary: summarize(answer),
    company_name: company?.name ?? null,
    created_at: new Date().toISOString(),
    used_tools,
    sources,
  }
  reportStore.push(report)

  return {
    answer,
    used_tools,
    sources,
    report_id: report.report_id,
    previous_report_id: report.previous_report_id,
  }
}

async function listReports(limit = 20, offset = 0) {
  await delay(200)
  const items = reportStore
    .slice()
    .reverse()
    .slice(offset, offset + limit)
    .map(toListItem)
  return { total: reportStore.length, items }
}

async function getReport(reportId: number) {
  await delay(150)
  const report = reportStore.find((r) => r.report_id === reportId)
  if (!report) throw notFound(reportId)
  return report
}

async function deleteReport(reportId: number) {
  await delay(150)
  const index = reportStore.findIndex((r) => r.report_id === reportId)
  if (index === -1) throw notFound(reportId)
  reportStore.splice(index, 1)
  // DB의 ON DELETE SET NULL 흉내: 이 리포트를 이어받던 후속 리포트의 링크를 끊는다.
  for (const r of reportStore) {
    if (r.previous_report_id === reportId) r.previous_report_id = null
  }
}

// ---------- 실시간 시세 (실제로는 네이버 비공식 폴링 API를 흉내) ----------

// 장중처럼 최근 종가 근처에서 살짝 흔들리는 "현재가"를 만든다. 호출마다 조금씩 바뀌어서
// 폴링(2~3초 간격)해도 값이 움직이는 걸 볼 수 있다.
function jitteredPrice(prevClose: number): number {
  const move = (Math.random() - 0.5) * 0.01
  return Math.max(100, Math.round((prevClose * (1 + move)) / 10) * 10)
}

async function getRealtimePrice(ticker: string): Promise<RealtimePrice> {
  await delay(150)
  const company = findCompany(ticker)
  if (!company) throw companyNotFound(ticker)

  const series = getPriceSeries(company.ticker)
  const prevClose = series[series.length - 2]?.close_price ?? series[series.length - 1].close_price
  const current = jitteredPrice(series[series.length - 1].close_price)

  return {
    ticker: company.ticker,
    company_name: company.name,
    current_price: current,
    change_amount: Math.round((current - prevClose) * 100) / 100,
    change_pct: Math.round(((current - prevClose) / prevClose) * 10000) / 100,
    as_of: new Date().toISOString(),
    queried_at: new Date().toISOString(),
    is_realtime: true,
    source: 'naver',
    corrected_from: null,
  }
}

// ---------- 오늘의 관심 종목 ----------

const WINDOW_DAYS = 20

async function getTrending(category: TrendingCategory, limit = 10): Promise<TrendingResponse> {
  await delay(200)

  const items: TrendingItem[] = COMPANIES.map((c) => {
    const series = getPriceSeries(c.ticker)
    const latest = series[series.length - 1]
    const window = series.slice(-1 - WINDOW_DAYS, -1)
    const avgVolume = window.reduce((sum, p) => sum + (p.volume ?? 0), 0) / (window.length || 1)
    return {
      ticker: c.ticker,
      company_name: c.name,
      close_price: latest.close_price,
      change_pct: latest.change_pct,
      volume: latest.volume,
      avg_volume: Math.round(avgVolume),
      volume_ratio: avgVolume > 0 ? Math.round(((latest.volume ?? 0) / avgVolume) * 100) / 100 : null,
    }
  })

  const sorted =
    category === 'gainers'
      ? items.sort((a, b) => (b.change_pct ?? 0) - (a.change_pct ?? 0))
      : category === 'losers'
        ? items.sort((a, b) => (a.change_pct ?? 0) - (b.change_pct ?? 0))
        : items.sort((a, b) => (b.volume_ratio ?? 0) - (a.volume_ratio ?? 0))

  return {
    category,
    price_date: getPriceSeries(COMPANIES[0].ticker).at(-1)!.price_date,
    window_days: WINDOW_DAYS,
    items: sorted.slice(0, limit),
  }
}

// ---------- 관심종목 ----------

const watchlistStore: { company: Company; added_at: string }[] = []

async function listWatchlist(): Promise<WatchlistItem[]> {
  await delay(200)
  return watchlistStore
    .slice()
    .reverse()
    .map(({ company, added_at }) => {
      const latest = getPriceSeries(company.ticker).at(-1)
      return {
        company_id: COMPANIES.indexOf(company) + 1,
        ticker: company.ticker,
        company_name: company.name,
        added_at,
        latest_close: latest?.close_price ?? null,
        latest_close_date: latest?.price_date ?? null,
        change_pct: latest?.change_pct ?? null,
      }
    })
}

async function addWatchlistItem(ticker: string): Promise<WatchlistItem> {
  await delay(200)
  const company = findCompany(ticker)
  if (!company) throw companyNotFound(ticker)
  if (watchlistStore.some((w) => w.company === company)) {
    throw new Error(`'${company.name}'는 이미 관심종목에 등록되어 있습니다.`)
  }
  const added_at = new Date().toISOString()
  watchlistStore.push({ company, added_at })
  // 실제 API와 동일하게, 추가 직후 응답에는 최근 종가를 다시 채워 넣지 않는다.
  return {
    company_id: COMPANIES.indexOf(company) + 1,
    ticker: company.ticker,
    company_name: company.name,
    added_at,
    latest_close: null,
    latest_close_date: null,
    change_pct: null,
  }
}

async function removeWatchlistItem(ticker: string): Promise<void> {
  await delay(200)
  const company = findCompany(ticker)
  if (!company) throw companyNotFound(ticker)
  const index = watchlistStore.findIndex((w) => w.company === company)
  if (index === -1) throw new Error(`'${company.name}'는 관심종목에 등록되어 있지 않습니다.`)
  watchlistStore.splice(index, 1)
}

// ---------- 모의투자 ----------

const INITIAL_CASH = 10_000_000
let cashBalance = INITIAL_CASH
const holdings = new Map<string, { company: Company; quantity: number; avg_price: number }>()

async function getPortfolio(): Promise<Portfolio> {
  await delay(200)
  const holdingItems: Holding[] = []
  for (const { company, quantity, avg_price } of holdings.values()) {
    const current = getPriceSeries(company.ticker).at(-1)!.close_price
    const eval_amount = Math.round(current * quantity)
    holdingItems.push({
      ticker: company.ticker,
      company_name: company.name,
      quantity,
      avg_price,
      current_price: current,
      is_realtime: true,
      eval_amount,
      profit_loss: Math.round(eval_amount - avg_price * quantity),
      profit_loss_pct: Math.round(((current - avg_price) / avg_price) * 10000) / 100,
    })
  }
  const total_eval_amount = holdingItems.reduce((sum, h) => sum + (h.eval_amount ?? 0), 0)
  return {
    cash_balance: cashBalance,
    holdings: holdingItems,
    total_eval_amount,
    total_asset: cashBalance + total_eval_amount,
    note: null,
  }
}

async function placeOrder(ticker: string, side: 'buy' | 'sell', quantity: number): Promise<OrderResult> {
  await delay(300)
  const company = findCompany(ticker)
  if (!company) throw companyNotFound(ticker)

  const price = getPriceSeries(company.ticker).at(-1)!.close_price
  const existing = holdings.get(company.ticker)

  if (side === 'buy') {
    const cost = price * quantity
    if (cost > cashBalance) {
      throw new Error(
        `잔고가 부족합니다(필요 ${Math.round(cost).toLocaleString('ko-KR')}원, 보유 ${cashBalance.toLocaleString('ko-KR')}원).`,
      )
    }
    cashBalance -= cost
    const newQuantity = (existing?.quantity ?? 0) + quantity
    const avg_price = existing
      ? (existing.quantity * existing.avg_price + quantity * price) / newQuantity
      : price
    holdings.set(company.ticker, { company, quantity: newQuantity, avg_price })
  } else {
    if (!existing || existing.quantity < quantity) {
      throw new Error(
        `보유 수량이 부족합니다(매도 요청 ${quantity}주, 보유 ${existing?.quantity ?? 0}주).`,
      )
    }
    cashBalance += price * quantity
    const remaining = existing.quantity - quantity
    if (remaining === 0) holdings.delete(company.ticker)
    else holdings.set(company.ticker, { ...existing, quantity: remaining })
  }

  const after = holdings.get(company.ticker)
  return {
    ticker: company.ticker,
    company_name: company.name,
    side,
    quantity,
    price,
    executed_at: new Date().toISOString(),
    cash_balance: cashBalance,
    holding: after ? { quantity: after.quantity, avg_price: after.avg_price } : null,
  }
}

export const mockApi: Api = {
  askResearch,
  listReports,
  getReport,
  deleteReport,

  getRealtimePrice,
  getTrending,

  listWatchlist,
  addWatchlistItem,
  removeWatchlistItem,
  getPortfolio,
  placeOrder,

  async listCompanies() {
    await delay(200)
    return COMPANIES
  },
  async getPrices(ticker, days) {
    await delay(300)
    return getPriceSeries(ticker).slice(-days)
  },
  async getNews(ticker, limit = 5) {
    await delay(300)
    const company = findCompany(ticker)
    return company ? makeNews(company.name, limit) : []
  },
  async getDisclosures(ticker, limit = 5) {
    await delay(300)
    const company = findCompany(ticker)
    return company ? makeDisclosures(company.name, limit) : []
  },
}
