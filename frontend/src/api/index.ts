import type {
  Company,
  DisclosureItem,
  NewsItem,
  PricePoint,
  ReportDetail,
  ReportList,
  ResearchResponse,
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

// 종목별 주가/뉴스/공시 조회. 백엔드에 대응하는 API가 아직 없어서
// USE_MOCK 여부와 무관하게 항상 mock 데이터를 쓴다.
export interface CompanyApi {
  listCompanies(): Promise<Company[]>
  getPrices(ticker: string, days: number): Promise<PricePoint[]>
  getNews(ticker: string, limit?: number): Promise<NewsItem[]>
  getDisclosures(ticker: string, limit?: number): Promise<DisclosureItem[]>
}

export type Api = ResearchApi & CompanyApi

export const USE_MOCK = import.meta.env.VITE_USE_MOCK !== 'false'

const research: ResearchApi = USE_MOCK ? mockApi : httpApi

export const api: Api = {
  ...research,
  listCompanies: mockApi.listCompanies,
  getPrices: mockApi.getPrices,
  getNews: mockApi.getNews,
  getDisclosures: mockApi.getDisclosures,
}
