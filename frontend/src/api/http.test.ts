import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from './errors'
import { httpApi } from './http'

function mockFetchOnce(body: unknown, status = 200): void {
  globalThis.fetch = vi.fn().mockResolvedValue({
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  }) as unknown as typeof fetch
}

const validRealtimePrice = {
  ticker: '005930',
  company_name: '삼성전자',
  current_price: 70000,
  change_amount: 700,
  change_pct: 1.01,
  as_of: '2026-10-13T00:00:00+09:00',
  queried_at: '2026-10-13T00:00:00+09:00',
  is_realtime: true,
  source: 'naver',
  corrected_from: null,
}

afterEach(() => {
  vi.restoreAllMocks()
})

describe('httpApi 응답 런타임 검증', () => {
  it('backend/API.md 그대로의 응답은 통과해서 그대로 쓸 수 있다', async () => {
    mockFetchOnce(validRealtimePrice)
    const data = await httpApi.getRealtimePrice('005930')
    expect(data.company_name).toBe('삼성전자')
    expect(data.current_price).toBe(70000)
  })

  it('필드 타입이 문서와 다르면(예: current_price가 문자열) ApiError(502)를 던지고 그 값을 그대로 넘기지 않는다', async () => {
    mockFetchOnce({ ...validRealtimePrice, current_price: '70000' })
    const err: unknown = await httpApi.getRealtimePrice('005930').catch((e) => e)
    expect(err).toBeInstanceOf(ApiError)
    expect((err as ApiError).status).toBe(502)
  })

  it('필드가 통째로 빠져 있어도(예: company_name 누락) 같은 방식으로 막는다', async () => {
    const { company_name, ...withoutName } = validRealtimePrice
    void company_name
    mockFetchOnce(withoutName)
    const err: unknown = await httpApi.getRealtimePrice('005930').catch((e) => e)
    expect(err).toBeInstanceOf(ApiError)
    expect((err as ApiError).status).toBe(502)
  })

  it('목록 응답(companies)도 배열 안 각 항목까지 검증한다', async () => {
    mockFetchOnce([{ ticker: '005930', name: '삼성전자', market: 'KOSPI', sector: null, latest_close: 'oops', change_pct: null }])
    const err: unknown = await httpApi.searchCompanies('삼성', 10).catch((e) => e)
    expect(err).toBeInstanceOf(ApiError)
    expect((err as ApiError).status).toBe(502)
  })
})
