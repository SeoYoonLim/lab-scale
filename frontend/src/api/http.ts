import type { ReportDetail, ReportList, ResearchResponse } from '../types'
import type { ResearchApi } from './index'

const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? '/api'

// FastAPI 검증 오류(422)는 detail이 [{msg, loc, ...}] 배열이고,
// 그 외 오류(404/502/503)는 detail이 문자열이다. (backend/API.md 참고)
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

export const httpApi: ResearchApi = {
  askResearch: (question, previousReportId) =>
    request<ResearchResponse>('/research', {
      method: 'POST',
      body: JSON.stringify({ question, previous_report_id: previousReportId ?? null }),
    }),

  listReports: (limit = 20, offset = 0) =>
    request<ReportList>(`/research?limit=${limit}&offset=${offset}`),

  getReport: (reportId) => request<ReportDetail>(`/research/${reportId}`),

  deleteReport: async (reportId) => {
    const res = await fetch(`${BASE_URL}/research/${reportId}`, { method: 'DELETE' })
    // 204 No Content: 본문이 없어서 res.json()을 호출하면 안 된다.
    if (!res.ok) {
      throw new Error(await readErrorMessage(res, `삭제에 실패했어요. (${res.status})`))
    }
  },
}
