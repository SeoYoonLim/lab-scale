import type {
  Company,
  DisclosureItem,
  NewsItem,
  OrderResult,
  Portfolio,
  PricePoint,
  ReportDetail,
  ReportList,
  ResearchResponse,
  RealtimePrice,
  TrendingCategory,
  TrendingResponse,
  WatchlistItem,
} from '../types'
import { httpApi } from './http'
import { mockApi } from './mock'

// 실제 백엔드(FastAPI, backend/API.md)와 연동되는 부분.
// VITE_USE_MOCK=false 로 전환하면 httpApi(/api 프록시 → localhost:8000)를 쓴다.
export interface ResearchApi {
  askResearch(question: string, previousReportId?: number | null): Promise<ResearchResponse>
  listReports(limit?: number, offset?: number): Promise<ReportList>
  getReport(reportId: number): Promise<ReportDetail>
  deleteReport(reportId: number): Promise<void>
}

// 로그인 없이 X-Device-Id 로 구분되는 관심종목·모의투자.
export interface AccountApi {
  listWatchlist(): Promise<WatchlistItem[]>
  addWatchlistItem(ticker: string): Promise<WatchlistItem>
  removeWatchlistItem(ticker: string): Promise<void>
  getPortfolio(): Promise<Portfolio>
  placeOrder(ticker: string, side: 'buy' | 'sell', quantity: number): Promise<OrderResult>
}

// 질문 없이 바로 호출하는 시세/스크리닝.
export interface MarketApi {
  getRealtimePrice(ticker: string): Promise<RealtimePrice>
  getTrending(category: TrendingCategory, limit?: number): Promise<TrendingResponse>
}

// 종목별 과거 주가/뉴스/공시 조회. 백엔드에 대응하는 API가 아직 없어서
// USE_MOCK 여부와 무관하게 항상 mock 데이터를 쓴다.
export interface CompanyApi {
  listCompanies(): Promise<Company[]>
  getPrices(ticker: string, days: number): Promise<PricePoint[]>
  getNews(ticker: string, limit?: number): Promise<NewsItem[]>
  getDisclosures(ticker: string, limit?: number): Promise<DisclosureItem[]>
}

export type Api = ResearchApi & AccountApi & MarketApi & CompanyApi

export const USE_MOCK = import.meta.env.VITE_USE_MOCK !== 'false'

const live: ResearchApi & AccountApi & MarketApi = USE_MOCK ? mockApi : httpApi

export const api: Api = {
  ...live,
  listCompanies: mockApi.listCompanies,
  getPrices: mockApi.getPrices,
  getNews: mockApi.getNews,
  getDisclosures: mockApi.getDisclosures,
}
