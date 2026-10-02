import { getDeviceId } from '../utils/deviceId'
import type {
  OrderResult,
  Portfolio,
  ReportDetail,
  ReportList,
  ResearchResponse,
  RealtimePrice,
  TrendingResponse,
  WatchlistItem,
} from '../types'
import type { AccountApi, MarketApi, ResearchApi } from './index'

const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? '/api'

// FastAPI 검증 오류(422)는 detail이 [{msg, loc, ...}] 배열이고,
// 그 외 오류(404/409/503 등)는 detail이 문자열이다. (backend/API.md 참고)
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

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE_URL}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  })
  if (!res.ok) {
    throw new Error(await readErrorMessage(res, `요청에 실패했어요. (${res.status})`))
  }
  return res.json() as Promise<T>
}

// 관심종목·모의투자는 모든 요청에 X-Device-Id가 필요하다(로그인이 아니라 단순 구분자).
function withDevice<T>(path: string, init?: RequestInit): Promise<T> {
  return request<T>(path, {
    ...init,
    headers: { ...init?.headers, 'X-Device-Id': getDeviceId() },
  })
}

async function requestVoid(path: string, init: RequestInit, fallback: string): Promise<void> {
  const res = await fetch(`${BASE_URL}${path}`, init)
  // 204 No Content: 본문이 없어서 res.json()을 호출하면 안 된다.
  if (!res.ok) {
    throw new Error(await readErrorMessage(res, fallback))
  }
}

const researchApi: ResearchApi = {
  askResearch: (question, previousReportId) =>
    request<ResearchResponse>('/research', {
      method: 'POST',
      body: JSON.stringify({ question, previous_report_id: previousReportId ?? null }),
    }),

  listReports: (limit = 20, offset = 0) =>
    request<ReportList>(`/research?limit=${limit}&offset=${offset}`),

  getReport: (reportId) => request<ReportDetail>(`/research/${reportId}`),

  deleteReport: (reportId) =>
    requestVoid(`/research/${reportId}`, { method: 'DELETE' }, `삭제에 실패했어요.`),
}

const marketApi: MarketApi = {
  getRealtimePrice: (ticker) =>
    request<RealtimePrice>(`/stocks/${encodeURIComponent(ticker)}/realtime-price`),

  getTrending: (category, limit = 10) =>
    request<TrendingResponse>(`/discovery/trending?category=${category}&limit=${limit}`),
}

const accountApi: AccountApi = {
  listWatchlist: () =>
    withDevice<{ items: WatchlistItem[] }>('/watchlist').then((res) => res.items),

  addWatchlistItem: (ticker) =>
    withDevice<WatchlistItem>('/watchlist', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ticker }),
    }),

  removeWatchlistItem: (ticker) =>
    requestVoid(
      `/watchlist/${encodeURIComponent(ticker)}`,
      { method: 'DELETE', headers: { 'X-Device-Id': getDeviceId() } },
      '관심종목 삭제에 실패했어요.',
    ),

  getPortfolio: () => withDevice<Portfolio>('/portfolio'),

  placeOrder: (ticker, side, quantity) =>
    withDevice<OrderResult>('/portfolio/orders', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ticker, side, quantity }),
    }),
}

export const httpApi: ResearchApi & AccountApi & MarketApi = {
  ...researchApi,
  ...marketApi,
  ...accountApi,
}
