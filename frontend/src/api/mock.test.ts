import { describe, expect, it, vi } from 'vitest'
import { mockApi } from './mock'
import { ApiError } from './errors'
import { setToken } from '../utils/token'

// 매번 새 아이디로 가입해서 테스트끼리 데이터(관심종목/보유종목)가 섞이지 않게 한다.
async function signupAndLogin(): Promise<void> {
  const username = `user${Math.random().toString(36).slice(2, 10)}`
  const res = await mockApi.signup(username, 'password123')
  setToken(res.access_token)
}

describe('mockApi watchlist', () => {
  it('addWatchlistItem이 중복 추가 시 ApiError(409)를 던진다', async () => {
    await signupAndLogin()
    await mockApi.addWatchlistItem('005930')

    const err: unknown = await mockApi.addWatchlistItem('005930').catch((e) => e)
    expect(err).toBeInstanceOf(ApiError)
    expect((err as ApiError).status).toBe(409)
  })

  it('addWatchlistItem 성공 응답에 최근 종가가 채워진다(2026-10-11 백엔드와 동일)', async () => {
    await signupAndLogin()
    const item = await mockApi.addWatchlistItem('005930')
    expect(item.latest_close).not.toBeNull()
  })

  it('모르는 종목이면 ApiError(404)를 던진다', async () => {
    await signupAndLogin()
    const err: unknown = await mockApi.addWatchlistItem('존재하지않는종목').catch((e) => e)
    expect(err).toBeInstanceOf(ApiError)
    expect((err as ApiError).status).toBe(404)
  })
})

describe('mockApi placeOrder', () => {
  it('수량이 정수가 아니면 ApiError(422)를 던진다', async () => {
    await signupAndLogin()
    const err: unknown = await mockApi.placeOrder('005930', 'buy', 1.5).catch((e) => e)
    expect(err).toBeInstanceOf(ApiError)
    expect((err as ApiError).status).toBe(422)
  })

  it('수량이 100,000주를 넘으면 ApiError(422)를 던진다', async () => {
    await signupAndLogin()
    const err: unknown = await mockApi.placeOrder('005930', 'buy', 100_001).catch((e) => e)
    expect(err).toBeInstanceOf(ApiError)
    expect((err as ApiError).status).toBe(422)
  })

  it('잔고가 부족하면 ApiError(400)를 던진다', async () => {
    await signupAndLogin()
    const err: unknown = await mockApi.placeOrder('005930', 'buy', 100_000).catch((e) => e)
    expect(err).toBeInstanceOf(ApiError)
    expect((err as ApiError).status).toBe(400)
  })
})

describe('mockApi getDiagnosis', () => {
  it('보유 종목이 없으면 ApiError(400)를 던진다', async () => {
    await signupAndLogin()
    const err: unknown = await mockApi.getDiagnosis().catch((e) => e)
    expect(err).toBeInstanceOf(ApiError)
    expect((err as ApiError).status).toBe(400)
  })
})

describe('mockApi auth', () => {
  it('로그인 실패(비밀번호 틀림)는 ApiError(401)를 던진다', async () => {
    const username = `user${Math.random().toString(36).slice(2, 10)}`
    await mockApi.signup(username, 'password123')
    const err: unknown = await mockApi.login(username, 'wrong-password').catch((e) => e)
    expect(err).toBeInstanceOf(ApiError)
    expect((err as ApiError).status).toBe(401)
  })

  it('토큰이 24시간 지나면 만료된 것으로 취급해 ApiError(401)를 던진다(backend/API.md와 동일한 TTL)', async () => {
    const now = Date.now()
    vi.spyOn(Date, 'now').mockReturnValue(now)
    await signupAndLogin()

    vi.spyOn(Date, 'now').mockReturnValue(now + 25 * 60 * 60 * 1000)
    const err: unknown = await mockApi.me().catch((e) => e)
    expect(err).toBeInstanceOf(ApiError)
    expect((err as ApiError).status).toBe(401)

    vi.restoreAllMocks()
  })
})
