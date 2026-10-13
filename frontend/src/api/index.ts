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
const rawUseMock = import.meta.env.VITE_USE_MOCK
if (rawUseMock !== undefined && rawUseMock !== 'true' && rawUseMock !== 'false') {
  // 오타(예: "False", "0")는 조용히 mock으로 빠지면 실제 API를 쓰려던 게 안 쓰여서 못 알아차리기 쉽다.
  console.warn(`VITE_USE_MOCK 값("${rawUseMock}")을 알 수 없어서 mock 모드로 동작해요. "true" 또는 "false"만 유효해요.`)
}
export const USE_MOCK = rawUseMock !== 'false'

export const api: Api = USE_MOCK ? mockApi : httpApi
