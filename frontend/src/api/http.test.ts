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

describe('httpApi 오류 응답 처리(핸드오프 6절)', () => {
  function mockErrorOnce(status: number, detail: unknown, headers: Record<string, string> = {}): void {
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status,
      headers: new Headers(headers),
      json: async () => ({ detail }),
    }) as unknown as typeof fetch
  }

  it('429는 Retry-After(초)를 메시지와 retryAfterSec에 담는다', async () => {
    mockErrorOnce(429, '요청이 너무 많습니다.', { 'Retry-After': '12' })
    const err: unknown = await httpApi.getRealtimePrice('005930').catch((e) => e)
    expect(err).toBeInstanceOf(ApiError)
    expect((err as ApiError).status).toBe(429)
    expect((err as ApiError).retryAfterSec).toBe(12)
    expect((err as ApiError).message).toBe('요청이 너무 많습니다. (12초 뒤 다시 시도해주세요)')
  })

  it('Retry-After가 없거나 숫자가 아니면 서버 문장만 그대로 쓴다', async () => {
    mockErrorOnce(429, '요청이 너무 많습니다.', { 'Retry-After': 'soon' })
    const err: unknown = await httpApi.getRealtimePrice('005930').catch((e) => e)
    expect((err as ApiError).message).toBe('요청이 너무 많습니다.')
    expect((err as ApiError).retryAfterSec).toBeUndefined()
  })

  it('pydantic이 앞에 붙이는 "Value error, "는 떼고 문장만 보여준다(실제 백엔드 가입 422)', async () => {
    mockErrorOnce(422, [{ loc: ['body', 'password'], msg: 'Value error, 너무 흔한 비밀번호입니다. 다른 비밀번호를 정해주세요.', type: 'value_error' }])
    const err: unknown = await httpApi.signup('someone', 'password123').catch((e) => e)
    expect((err as ApiError).message).toBe('너무 흔한 비밀번호입니다. 다른 비밀번호를 정해주세요.')
  })

  it('422는 detail 배열의 msg를 이어 붙이고, 그 밖의 오류는 detail 문자열을 그대로 쓴다', async () => {
    mockErrorOnce(422, [{ loc: ['query', 'days'], msg: 'Input should be less than or equal to 90', type: 'less_than_equal' }])
    const validation: unknown = await httpApi.getRealtimePrice('005930').catch((e) => e)
    expect((validation as ApiError).status).toBe(422)
    expect((validation as ApiError).message).toBe('Input should be less than or equal to 90')

    mockErrorOnce(404, "'없는종목' 종목을 찾지 못했습니다.")
    const missing: unknown = await httpApi.getRealtimePrice('없는종목').catch((e) => e)
    expect((missing as ApiError).message).toBe("'없는종목' 종목을 찾지 못했습니다.")
  })
})
