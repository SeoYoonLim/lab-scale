import type {
  Company,
  DisclosureItem,
  NewsItem,
  PricePoint,
  ResearchResponse,
  ToolCall,
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

export const mockApi: Api = {
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

  // 백엔드 에이전트(scripts/test_tool_calling.py)의 도구 선택 규칙을 흉내 낸다.
  async askResearch(question) {
    await delay(1200)

    const company = COMPANIES.find((c) => question.includes(c.name) || question.includes(c.ticker))
    const wantsDisclosure = /공시|사업보고서|자사주|공식/.test(question)
    const wantsNews = /뉴스|이슈|왜|소식/.test(question)
    const wantsStock = /주가|등락|거래량|오른|올랐|내렸|내린|하락|상승|시세/.test(question)
    const daysMatch = question.match(/(\d+)\s*일/)
    const periodDays = daysMatch ? Math.min(Number(daysMatch[1]), 90) : 1

    const toolCalls: ToolCall[] = []
    const summary: string[] = []

    if (company) {
      const useStock = wantsStock || (!wantsNews && !wantsDisclosure)

      if (useStock) {
        const prices = getPriceSeries(company.ticker).slice(-periodDays)
        const latest = prices[prices.length - 1]
        toolCalls.push({
          tool_name: 'stock_tool',
          arguments: { ticker: company.name, period_days: periodDays },
          result: {
            ticker: company.name,
            period_days: periodDays,
            found: true,
            change_pct: latest.change_pct,
            volume: latest.volume,
            prices,
          },
        })
        summary.push(
          `최근 ${periodDays}일 기준으로 가장 최근 등락률은 ${latest.change_pct}%, 거래량은 ${latest.volume?.toLocaleString('ko-KR')}주예요.`,
        )
      }
      if (wantsNews) {
        const news = makeNews(company.name, 5)
        toolCalls.push({
          tool_name: 'news_tool',
          arguments: { company_name: company.name, limit: 5 },
          result: { company_name: company.name, limit: 5, found: true, news },
        })
        summary.push(`관련 뉴스 ${news.length}건을 확인했어요.`)
      }
      if (wantsDisclosure) {
        const disclosures = makeDisclosures(company.name, 5)
        toolCalls.push({
          tool_name: 'disclosure_tool',
          arguments: { company_name: company.name, limit: 5 },
          result: { company_name: company.name, limit: 5, found: true, disclosures },
        })
        summary.push(`최근 공시 ${disclosures.length}건을 확인했어요.`)
      }
    }

    const answer = company
      ? `[샘플 응답] ${company.name}에 대해 조회했어요. ${summary.join(' ')}\n실제 백엔드가 연결되면 모델이 도구 결과를 바탕으로 만든 답변이 여기에 표시돼요.`
      : '[샘플 응답] 도구를 호출하지 않고 바로 답하는 경우예요. 종목명(삼성전자, SK하이닉스, NAVER)을 넣어서 질문하면 도구 호출 결과도 함께 볼 수 있어요.'

    const response: ResearchResponse = { answer, tool_calls: toolCalls }
    return response
  },
}
