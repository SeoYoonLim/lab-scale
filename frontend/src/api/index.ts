import type {
  AuthResponse,
  Company,
  DiagnosisResponse,
  OrderResult,
  Portfolio,
  ReportDetail,
  ReportList,
  ResearchResponse,
  RealtimePrice,
  TradeList,
  TrendingCategory,
  TrendingResponse,
  User,
  WatchlistItem,
} from '../types'
import { httpApi } from './http'
import { mockApi } from './mock'

export interface AuthApi {
  signup(username: string, password: string): Promise<AuthResponse>
  login(username: string, password: string): Promise<AuthResponse>
  me(): Promise<User>
}

// 로그인한 사용자 본인 것만 다루는 API(Authorization: Bearer 필요).
export interface ResearchApi {
  askResearch(question: string, previousReportId?: number | null): Promise<ResearchResponse>
  listReports(limit?: number, offset?: number): Promise<ReportList>
  getReport(reportId: number): Promise<ReportDetail>
  deleteReport(reportId: number): Promise<void>
}

export interface AccountApi {
  listWatchlist(): Promise<WatchlistItem[]>
  addWatchlistItem(ticker: string): Promise<WatchlistItem>
  removeWatchlistItem(ticker: string): Promise<void>
  getPortfolio(): Promise<Portfolio>
  placeOrder(ticker: string, side: 'buy' | 'sell', quantity: number): Promise<OrderResult>
  getTrades(limit?: number, offset?: number, ticker?: string): Promise<TradeList>
  resetPortfolio(): Promise<Portfolio>
  getDiagnosis(): Promise<DiagnosisResponse>
}

// 질문/로그인 없이 바로 부르는 API.
export interface PublicApi {
  getRealtimePrice(ticker: string): Promise<RealtimePrice>
  getTrending(category: TrendingCategory, limit?: number): Promise<TrendingResponse>
  searchCompanies(q?: string, limit?: number): Promise<Company[]>
  getDisclaimer(): Promise<string>
}

export type Api = AuthApi & ResearchApi & AccountApi & PublicApi

// 2026-10-07: 로그인 도입으로 모든 데이터가 실제 백엔드로 연동 가능해져서, mock 여부는
// 더 이상 기능별로 나뉘지 않고 이 플래그 하나로 앱 전체가 토글된다.
export const USE_MOCK = import.meta.env.VITE_USE_MOCK !== 'false'

export const api: Api = USE_MOCK ? mockApi : httpApi
