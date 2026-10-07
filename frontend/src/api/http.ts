import { AUTH_EXPIRED_EVENT, clearToken, getToken } from '../utils/token'
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
  TrendingResponse,
  User,
  WatchlistItem,
} from '../types'
import type { Api } from './index'

const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? '/api'

// FastAPI 검증 오류(422)는 detail이 [{msg, loc, ...}] 배열이고,
// 그 외 오류(400/401/404/409/502/503)는 detail이 문자열이다. (backend/API.md 참고)
async function readErrorMessage(res: Response, fallback: string): Promise<string> {
  try {
    const body = (await res.json()) as { detail?: unknown }
    if (typeof body.detail === 'string') return body.detail
    if (Array.isArray(body.detail)) {
      return body.detail.map((e) => (e as { msg?: string }).msg).filter(Boolean).join(' / ') || fallback
    }
  } catch {
    // JSON이 아닌 응답(예: 500의 plain text)은 fallback을 쓴다.
  }
  return fallback
}

function authHeader(): Record<string, string> {
  const token = getToken()
  return token ? { Authorization: `Bearer ${token}` } : {}
}

// auth=true면 로그인 필요 API다: 토큰을 Authorization 헤더로 싣고, 401이면 토큰을 지우고
// AUTH_EXPIRED_EVENT를 쏜다(AuthContext가 듣고 로그인 화면으로 돌려보낸다).
async function request<T>(path: string, init: RequestInit = {}, auth = false): Promise<T> {
  const res = await fetch(`${BASE_URL}${path}`, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      ...(auth ? authHeader() : {}),
      ...init.headers,
    },
  })
  if (auth && res.status === 401) {
    clearToken()
    window.dispatchEvent(new Event(AUTH_EXPIRED_EVENT))
  }
  if (!res.ok) {
    throw new Error(await readErrorMessage(res, `요청에 실패했어요. (${res.status})`))
  }
  return res.json() as Promise<T>
}

async function requestVoid(path: string, init: RequestInit, fallback: string, auth = false): Promise<void> {
  const res = await fetch(`${BASE_URL}${path}`, {
    ...init,
    headers: { ...(auth ? authHeader() : {}), ...init.headers },
  })
  if (auth && res.status === 401) {
    clearToken()
    window.dispatchEvent(new Event(AUTH_EXPIRED_EVENT))
  }
  // 204 No Content: 본문이 없어서 res.json()을 호출하면 안 된다.
  if (!res.ok) {
    throw new Error(await readErrorMessage(res, fallback))
  }
}

function qs(params: Record<string, string | number | undefined>): string {
  const entries = Object.entries(params).filter(([, v]) => v !== undefined) as [string, string | number][]
  if (entries.length === 0) return ''
  return `?${new URLSearchParams(entries.map(([k, v]) => [k, String(v)])).toString()}`
}

export const httpApi: Api = {
  signup: (username, password) =>
    request<AuthResponse>('/auth/signup', { method: 'POST', body: JSON.stringify({ username, password }) }),
  login: (username, password) =>
    request<AuthResponse>('/auth/login', { method: 'POST', body: JSON.stringify({ username, password }) }),
  me: () => request<User>('/auth/me', {}, true),

  askResearch: (question, previousReportId) =>
    request<ResearchResponse>(
      '/research',
      { method: 'POST', body: JSON.stringify({ question, previous_report_id: previousReportId ?? null }) },
      true,
    ),
  listReports: (limit = 20, offset = 0) =>
    request<ReportList>(`/research${qs({ limit, offset })}`, {}, true),
  getReport: (reportId) => request<ReportDetail>(`/research/${reportId}`, {}, true),
  deleteReport: (reportId) => requestVoid(`/research/${reportId}`, { method: 'DELETE' }, '삭제에 실패했어요.', true),

  listWatchlist: () => request<{ items: WatchlistItem[] }>('/watchlist', {}, true).then((res) => res.items),
  addWatchlistItem: (ticker) =>
    request<WatchlistItem>('/watchlist', { method: 'POST', body: JSON.stringify({ ticker }) }, true),
  removeWatchlistItem: (ticker) =>
    requestVoid(
      `/watchlist/${encodeURIComponent(ticker)}`,
      { method: 'DELETE' },
      '관심종목 삭제에 실패했어요.',
      true,
    ),

  getPortfolio: () => request<Portfolio>('/portfolio', {}, true),
  placeOrder: (ticker, side, quantity) =>
    request<OrderResult>('/portfolio/orders', { method: 'POST', body: JSON.stringify({ ticker, side, quantity }) }, true),
  getTrades: (limit = 50, offset = 0, ticker) =>
    request<TradeList>(`/portfolio/trades${qs({ limit, offset, ticker })}`, {}, true),
  resetPortfolio: () =>
    request<Portfolio>('/portfolio/reset', { method: 'POST', body: JSON.stringify({ confirm: true }) }, true),
  getDiagnosis: () => request<DiagnosisResponse>('/portfolio/diagnosis', { method: 'POST' }, true),

  getRealtimePrice: (ticker) => request<RealtimePrice>(`/stocks/${encodeURIComponent(ticker)}/realtime-price`),
  getTrending: (category, limit = 10) => request<TrendingResponse>(`/discovery/trending${qs({ category, limit })}`),
  searchCompanies: (q, limit) => request<Company[]>(`/companies${qs({ q, limit })}`),
  getDisclaimer: () => request<{ text: string }>('/disclaimer').then((res) => res.text),
}
