import { ApiError } from './errors'
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

// 기본 타임아웃. 리서치/진단은 LLM을 기다려야 해서 훨씬 오래 걸릴 수 있다고 backend/API.md에 적혀
// 있으므로(각각 60초, 90초 이상 권장) 그 둘만 따로 늘려서 부른다.
const DEFAULT_TIMEOUT_MS = 20_000
const RESEARCH_TIMEOUT_MS = 60_000
const DIAGNOSIS_TIMEOUT_MS = 90_000

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

// request()/requestVoid() 둘 다 쓰는 공통 부분: fetch + 타임아웃 + 인증 헤더 + 401 처리.
// 응답 본문을 읽는 방식(JSON 있음/없음)만 다르므로 그건 각자 알아서 한다.
async function doFetch(path: string, init: RequestInit, auth: boolean, timeoutMs: number): Promise<Response> {
  const controller = new AbortController()
  const timeoutId = setTimeout(() => controller.abort(), timeoutMs)
  try {
    const res = await fetch(`${BASE_URL}${path}`, {
      ...init,
      headers: { ...(auth ? authHeader() : {}), ...init.headers },
      signal: controller.signal,
    })
    if (auth && res.status === 401) {
      clearToken()
      window.dispatchEvent(new Event(AUTH_EXPIRED_EVENT))
    }
    return res
  } catch (err) {
    if (err instanceof DOMException && err.name === 'AbortError') {
      // status 0: ApiError.status===401/409 같은 분기에 절대 안 걸리게, 실제 HTTP 상태와는 구분되는 값.
      throw new ApiError('요청이 너무 오래 걸려서 중단했어요. 네트워크를 확인하고 다시 시도해주세요.', 0)
    }
    throw err
  } finally {
    clearTimeout(timeoutId)
  }
}

async function request<T>(
  path: string,
  init: RequestInit = {},
  auth = false,
  timeoutMs = DEFAULT_TIMEOUT_MS,
): Promise<T> {
  const res = await doFetch(
    path,
    { ...init, headers: { 'Content-Type': 'application/json', ...init.headers } },
    auth,
    timeoutMs,
  )
  if (!res.ok) {
    throw new ApiError(await readErrorMessage(res, `요청에 실패했어요. (${res.status})`), res.status)
  }
  return res.json() as Promise<T>
}

async function requestVoid(
  path: string,
  init: RequestInit,
  fallback: string,
  auth = false,
  timeoutMs = DEFAULT_TIMEOUT_MS,
): Promise<void> {
  const res = await doFetch(path, init, auth, timeoutMs)
  // 204 No Content: 본문이 없어서 res.json()을 호출하면 안 된다.
  if (!res.ok) {
    throw new ApiError(await readErrorMessage(res, fallback), res.status)
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
      RESEARCH_TIMEOUT_MS,
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
  getDiagnosis: () =>
    request<DiagnosisResponse>('/portfolio/diagnosis', { method: 'POST' }, true, DIAGNOSIS_TIMEOUT_MS),

  getRealtimePrice: (ticker) => request<RealtimePrice>(`/stocks/${encodeURIComponent(ticker)}/realtime-price`),
  getTrending: (category, limit = 10) => request<TrendingResponse>(`/discovery/trending${qs({ category, limit })}`),
  searchCompanies: (q, limit) => request<Company[]>(`/companies${qs({ q, limit })}`),
  getDisclaimer: () => request<{ text: string }>('/disclaimer').then((res) => res.text),
}
