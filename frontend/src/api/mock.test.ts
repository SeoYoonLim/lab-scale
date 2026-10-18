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

describe('mockApi ETF (2026-10-14 추가)', () => {
  it('searchCompanies(asset_type=etf)는 ETF만 돌려주고, 전체 응답에 asset_type이 들어 있다', async () => {
    const all = await mockApi.searchCompanies(undefined, 50)
    expect(all.some((c) => c.asset_type === 'stock')).toBe(true)
    expect(all.some((c) => c.asset_type === 'etf')).toBe(true)

    const etfOnly = await mockApi.searchCompanies(undefined, 50, 'etf')
    expect(etfOnly.length).toBeGreaterThan(0)
    expect(etfOnly.every((c) => c.asset_type === 'etf')).toBe(true)
    // ETF는 market/sector가 null(backend/API.md)
    expect(etfOnly.every((c) => c.market === null)).toBe(true)
  })

  it('getTrending 기본값은 주식만이고, asset_type=etf를 주면 ETF만 나온다', async () => {
    const stockDefault = await mockApi.getTrending('gainers', 5)
    expect(stockDefault.asset_type).toBe('stock')

    const etf = await mockApi.getTrending('gainers', 5, 'etf')
    expect(etf.asset_type).toBe('etf')
    expect(etf.items.length).toBeGreaterThan(0)
  })

  it('ETF를 보유하면 종목 수가 3개 미만이어도 FEW_HOLDINGS 대신 strengths 안내가 들어간다', async () => {
    await signupAndLogin()
    await mockApi.placeOrder('069500', 'buy', 1) // KODEX 200, ETF 1종목만 보유
    const result = await mockApi.getDiagnosis()
    expect(result.flags.some((f) => f.code === 'FEW_HOLDINGS')).toBe(false)
    expect(result.strengths.some((s) => s.includes('ETF'))).toBe(true)
  })

  it('레버리지 ETF를 보유하면 LEVERAGED_INVERSE_ETF 플래그가 붙는다', async () => {
    await signupAndLogin()
    await mockApi.placeOrder('122630', 'buy', 1) // KODEX 레버리지
    const result = await mockApi.getDiagnosis()
    const flag = result.flags.find((f) => f.code === 'LEVERAGED_INVERSE_ETF')
    expect(flag).toBeDefined()
    expect(flag?.ticker).toBe('122630')
  })

  it('ETF는 시장 쏠림(MARKET_SKEW) 계산에서 빠진다', async () => {
    await signupAndLogin()
    // 주식 1종목 + ETF 1종목: 주식만 보면 그 시장이 100%라 MARKET_SKEW가 걸려야 한다.
    await mockApi.placeOrder('005930', 'buy', 1)
    await mockApi.placeOrder('069500', 'buy', 1)
    const result = await mockApi.getDiagnosis()
    const skew = result.flags.find((f) => f.code === 'MARKET_SKEW')
    expect(skew?.market).toBe('KOSPI')
    const etfHolding = result.holdings.find((h) => h.ticker === '069500')
    expect(etfHolding?.market).toBe('ETF')
    expect(etfHolding?.asset_type).toBe('etf')
  })
})

describe('mockApi 진단 이력(2026-10-14 추가)', () => {
  it('진단마다 diagnosis_id/created_at이 채워지고, GET로 그대로 조회된다', async () => {
    await signupAndLogin()
    await mockApi.placeOrder('005930', 'buy', 1)
    const posted = await mockApi.getDiagnosis()
    expect(posted.diagnosis_id).not.toBeNull()
    expect(posted.created_at).not.toBeNull()

    const detail = await mockApi.getDiagnosisDetail(posted.diagnosis_id!)
    expect(detail.id).toBe(posted.diagnosis_id)
    expect(detail.summary).toBe(posted.summary)
  })

  it('남의(혹은 없는) 진단 id는 404다', async () => {
    await signupAndLogin()
    await mockApi.placeOrder('005930', 'buy', 1)
    const posted = await mockApi.getDiagnosis()

    await signupAndLogin() // 다른 사용자로 전환
    const err: unknown = await mockApi.getDiagnosisDetail(posted.diagnosis_id!).catch((e) => e)
    expect(err).toBeInstanceOf(ApiError)
    expect((err as ApiError).status).toBe(404)
  })

  it('첫 진단의 compare는 previous/changes가 null이고, 두 번째 진단은 변화가 계산된다', async () => {
    await signupAndLogin()
    await mockApi.placeOrder('005930', 'buy', 2)
    const first = await mockApi.getDiagnosis()

    const firstCompare = await mockApi.compareDiagnosis(first.diagnosis_id!)
    expect(firstCompare.previous).toBeNull()
    expect(firstCompare.changes).toBeNull()

    await mockApi.placeOrder('035720', 'buy', 3) // 카카오 신규 편입
    const second = await mockApi.getDiagnosis()

    const compare = await mockApi.compareDiagnosis(second.diagnosis_id!)
    expect(compare.previous?.id).toBe(first.diagnosis_id)
    expect(compare.changes?.holdings.some((h) => h.ticker === '035720' && h.status === 'added')).toBe(true)
  })

  it('getDiagnosisHistory는 최신순으로 나오고, 삭제하면 목록과 조회 모두에서 사라진다', async () => {
    await signupAndLogin()
    await mockApi.placeOrder('005930', 'buy', 1)
    const first = await mockApi.getDiagnosis()
    await mockApi.placeOrder('035720', 'buy', 1)
    const second = await mockApi.getDiagnosis()

    const history = await mockApi.getDiagnosisHistory(20, 0)
    expect(history.items[0].id).toBe(second.diagnosis_id)
    expect(history.items[1].id).toBe(first.diagnosis_id)

    await mockApi.deleteDiagnosis(first.diagnosis_id!)
    const afterDelete = await mockApi.getDiagnosisHistory(20, 0)
    expect(afterDelete.items.some((i) => i.id === first.diagnosis_id)).toBe(false)

    const err: unknown = await mockApi.getDiagnosisDetail(first.diagnosis_id!).catch((e) => e)
    expect(err).toBeInstanceOf(ApiError)
    expect((err as ApiError).status).toBe(404)
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

describe('mockApi 데이터 기준일·사건 반응(2026-10-15 추가)', () => {
  it('getStatus는 종가 기준(is_realtime=false)이고 기준일이 있다', async () => {
    const status = await mockApi.getStatus()
    expect(status.is_realtime).toBe(false)
    expect(status.sources.stock_price.latest_date).toMatch(/^\d{4}-\d{2}-\d{2}$/)
  })

  it('getEventReactions는 주식이면 공시·뉴스를 모두, 유형 필터를 주면 그 유형만 준다', async () => {
    const all = await mockApi.getEventReactions('005930')
    expect(all.asset_type).toBe('stock')
    expect(all.market_index).toBe('KS11')
    expect(new Set(all.events.map((e) => e.event_type))).toEqual(new Set(['disclosure', 'news']))
    expect(all.summary.event_count).toBe(all.events.length)

    const news = await mockApi.getEventReactions('005930', { eventType: 'news' })
    expect(news.events.length).toBeGreaterThan(0)
    expect(news.events.every((e) => e.event_type === 'news')).toBe(true)
  })

  it('아직 N거래일이 안 지난 최근 사건은 pct=null과 사유를 준다', async () => {
    const { events } = await mockApi.getEventReactions('005930')
    const latest = events[0]
    expect(latest.returns.d20.pct).toBeNull()
    expect(latest.returns.d20.available).toBe(false)
    expect(latest.returns.d20.reason).toContain('거래일 경과 전')
  })

  it('ETF는 뉴스만 대상이고 지수 대비 초과수익률이 null이다', async () => {
    const res = await mockApi.getEventReactions('069500')
    expect(res.asset_type).toBe('etf')
    expect(res.market_index).toBeNull()
    expect(res.events.every((e) => e.event_type === 'news')).toBe(true)
    expect(res.events.every((e) => e.returns.d1.excess_pct === null)).toBe(true)
  })

  it('모르는 종목은 404, 범위를 벗어난 limit은 422', async () => {
    await expect(mockApi.getEventReactions('없는종목')).rejects.toMatchObject({ status: 404 })
    await expect(mockApi.getEventReactions('005930', { limit: 31 })).rejects.toMatchObject({ status: 422 })
  })
})

describe('mockApi getPriceHistory (2026-10-19 추가)', () => {
  it('날짜 오름차순이고 count는 실제 items 개수다', async () => {
    const res = await mockApi.getPriceHistory('005930', 20)
    expect(res.count).toBe(res.items.length)
    expect(res.count).toBe(20)
    expect(res.days).toBe(20)
    expect(res.price_as_of).toBe(res.items.at(-1)?.date)
    const dates = res.items.map((i) => i.date)
    expect([...dates].sort()).toEqual(dates)
  })

  it('데이터 시작일 때문에 days=90이어도 개수가 모자란다 - 주식 73, ETF 59(backend/API.md 실측)', async () => {
    const stock = await mockApi.getPriceHistory('005930', 90)
    expect(stock).toMatchObject({ days: 90, count: 73, asset_type: 'stock' })
    const etf = await mockApi.getPriceHistory('069500', 90)
    expect(etf).toMatchObject({ days: 90, count: 59, asset_type: 'etf' })
  })

  it('모르는 종목은 404, 범위를 벗어난 days는 422', async () => {
    await expect(mockApi.getPriceHistory('없는종목')).rejects.toMatchObject({ status: 404 })
    await expect(mockApi.getPriceHistory('005930', 91)).rejects.toMatchObject({ status: 422 })
    await expect(mockApi.getPriceHistory('005930', 0)).rejects.toMatchObject({ status: 422 })
  })

  it('고가 >= 종가/시가 >= 저가 관계를 지킨다', async () => {
    const { items } = await mockApi.getPriceHistory('035720', 60)
    for (const i of items) {
      expect(i.high!).toBeGreaterThanOrEqual(Math.max(i.open!, i.close))
      expect(i.low!).toBeLessThanOrEqual(Math.min(i.open!, i.close))
    }
  })
})
