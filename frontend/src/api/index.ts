import type {
  AssetType,
  AuthResponse,
  Company,
  DiagnosisCompareResponse,
  DiagnosisDetail,
  DiagnosisHistoryList,
  DiagnosisResponse,
  EventReactionsResponse,
  EventTypeFilter,
  OrderResult,
  Portfolio,
  PriceHistoryResponse,
  ReportDetail,
  ReportList,
  ResearchResponse,
  RealtimePrice,
  StatusResponse,
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
  // 진단 이력(2026-10-14 추가): 성공한 진단은 저장되고(diagnosis_id), 이력에서 다시 보거나 비교할 수 있다.
  getDiagnosisHistory(limit?: number, offset?: number): Promise<DiagnosisHistoryList>
  getDiagnosisDetail(diagnosisId: number): Promise<DiagnosisDetail>
  compareDiagnosis(diagnosisId: number, baseId?: number): Promise<DiagnosisCompareResponse>
  deleteDiagnosis(diagnosisId: number): Promise<void>
}

// 질문/로그인 없이 바로 부르는 API.
export interface PublicApi {
  getRealtimePrice(ticker: string): Promise<RealtimePrice>
  getTrending(category: TrendingCategory, limit?: number, assetType?: AssetType): Promise<TrendingResponse>
  searchCompanies(q?: string, limit?: number, assetType?: AssetType): Promise<Company[]>
  getDisclaimer(): Promise<string>
  // 주가 추이 차트용 일별 시세. days는 "최근 N거래일"(1~90)이고 데이터가 모자라면 count < days다.
  getPriceHistory(ticker: string, days?: number): Promise<PriceHistoryResponse>
  // 데이터 기준일(소스별 최신 날짜, 갱신 결과). 종가 기준 DB 값이라 실시간이 아니다.
  getStatus(): Promise<StatusResponse>
  // 뉴스·공시 이후 1/5/20거래일 주가 반응. 코드로 계산한 값이고 인과관계가 아니다. limit은 유형별 건수(1~30).
  getEventReactions(
    ticker: string,
    options?: { eventType?: EventTypeFilter; limit?: number; sinceDays?: number },
  ): Promise<EventReactionsResponse>
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
