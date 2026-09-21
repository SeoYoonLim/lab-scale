import type {
  Company,
  DisclosureItem,
  NewsItem,
  PricePoint,
  ResearchResponse,
  ToolCall,
} from '../types'
import type { Api } from './index'

const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? '/api'

// /research 응답 형식은 아직 확정 전이라 두 가지를 모두 받는다.
//   현재 백엔드: { answer, used_tools: string[] }
//   제안 형식:   { answer, tool_calls: [{ tool_name, arguments, result }] }
interface RawResearchResponse {
  answer: string
  used_tools?: string[]
  tool_calls?: ToolCall[]
}

function normalizeResearch(raw: RawResearchResponse): ResearchResponse {
  const tool_calls = raw.tool_calls ?? (raw.used_tools ?? []).map((tool_name) => ({ tool_name }))
  return { answer: raw.answer, tool_calls }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE_URL}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  })
  if (!res.ok) {
    throw new Error(`요청에 실패했어요. (${res.status})`)
  }
  return res.json() as Promise<T>
}

// 백엔드 API 는 아직 없어서, 아래 엔드포인트는 프론트에서 제안한 형식이다.
export const httpApi: Api = {
  listCompanies: () => request<Company[]>('/companies'),
  getPrices: (ticker, days) =>
    request<PricePoint[]>(`/companies/${encodeURIComponent(ticker)}/prices?days=${days}`),
  getNews: (ticker, limit = 5) =>
    request<NewsItem[]>(`/companies/${encodeURIComponent(ticker)}/news?limit=${limit}`),
  getDisclosures: (ticker, limit = 5) =>
    request<DisclosureItem[]>(`/companies/${encodeURIComponent(ticker)}/disclosures?limit=${limit}`),
  askResearch: (question) =>
    request<RawResearchResponse>('/research', {
      method: 'POST',
      body: JSON.stringify({ question }),
    }).then(normalizeResearch),
}
