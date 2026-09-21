import type {
  Company,
  DisclosureItem,
  NewsItem,
  PricePoint,
  ResearchResponse,
} from '../types'
import { httpApi } from './http'
import { mockApi } from './mock'

export interface Api {
  listCompanies(): Promise<Company[]>
  // 최근 days 거래일치 주가. 날짜 오름차순.
  getPrices(ticker: string, days: number): Promise<PricePoint[]>
  getNews(ticker: string, limit?: number): Promise<NewsItem[]>
  getDisclosures(ticker: string, limit?: number): Promise<DisclosureItem[]>
  askResearch(question: string): Promise<ResearchResponse>
}

// 백엔드 API 가 준비되면 .env 에 VITE_USE_MOCK=false 를 넣어 실제 API 로 전환한다.
export const USE_MOCK = import.meta.env.VITE_USE_MOCK !== 'false'

export const api: Api = USE_MOCK ? mockApi : httpApi
