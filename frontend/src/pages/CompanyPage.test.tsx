import { act, render } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import CompanyPage from './CompanyPage'
import type { RealtimePrice } from '../types'

vi.mock('../api', () => ({
  api: {
    getRealtimePrice: vi.fn(),
    addWatchlistItem: vi.fn(),
  },
}))
vi.mock('../auth/useAuth', () => ({ useAuth: () => ({ user: null }) }))

const { api } = await import('../api')
const mockGetPrice = api.getRealtimePrice as unknown as ReturnType<typeof vi.fn>

function price(overrides: Partial<RealtimePrice> = {}): RealtimePrice {
  return {
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
    ...overrides,
  }
}

function renderCompanyPage(ticker = '005930') {
  return render(
    <MemoryRouter initialEntries={[`/companies/${ticker}`]}>
      <Routes>
        <Route path="/companies/:ticker" element={<CompanyPage />} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('CompanyPage 실시간 시세 폴링', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    mockGetPrice.mockReset()
  })
  afterEach(() => {
    vi.useRealTimers()
  })

  it('이전 요청이 3초(폴링 주기)보다 느리게 끝나도 다음 요청을 겹쳐서 보내지 않는다', async () => {
    let resolveFirst: ((v: RealtimePrice) => void) | undefined
    const firstPromise = new Promise<RealtimePrice>((resolve) => {
      resolveFirst = resolve
    })
    mockGetPrice.mockReturnValueOnce(firstPromise)
    mockGetPrice.mockReturnValue(new Promise(() => {})) // 이후 호출은 더 안 끝나도 됨

    renderCompanyPage()
    expect(mockGetPrice).toHaveBeenCalledTimes(1)

    // setInterval이었다면 9초 동안 2~3번 더 나갔을 것 - "끝나야 다음 걸 예약"하는 지금 구조에서는
    // 첫 요청이 안 끝난 이상 몇 초가 지나도 두 번째 요청이 나가면 안 된다.
    await vi.advanceTimersByTimeAsync(9000)
    expect(mockGetPrice).toHaveBeenCalledTimes(1)

    resolveFirst?.(price())
    await act(() => vi.advanceTimersByTimeAsync(0)) // .then 콜백이 실행되도록 마이크로태스크를 흘려보낸다

    // 이제서야 다음 폴링이 3초 뒤로 예약된다.
    await act(() => vi.advanceTimersByTimeAsync(3000))
    expect(mockGetPrice).toHaveBeenCalledTimes(2)
  })

  it('한 번 성공한 뒤 폴링이 실패하면 가격은 유지하면서 "최신화 실패" 안내를 보여주고, 다시 성공하면 안내가 사라진다', async () => {
    mockGetPrice.mockResolvedValueOnce(price())
    mockGetPrice.mockRejectedValueOnce(new Error('일시적인 네트워크 오류'))
    mockGetPrice.mockResolvedValueOnce(price())
    mockGetPrice.mockReturnValue(new Promise(() => {}))

    // fake timers가 켜져 있으면 findByText의 내부 폴링(setTimeout 기반)도 멈춰서 타임아웃나므로,
    // 매 advanceTimersByTimeAsync 뒤에는 동기 쿼리(getByText/queryByText)로 바로 확인한다.
    const { getByText, queryByText } = renderCompanyPage()
    await act(() => vi.advanceTimersByTimeAsync(10))
    expect(getByText('삼성전자')).toBeTruthy()

    await act(() => vi.advanceTimersByTimeAsync(3000))
    // 실패했어도 이전 성공 응답(삼성전자/가격)이 화면에서 사라지지 않는다 - 그냥 에러 화면으로 안 바뀐다.
    expect(getByText(/최신 시세를 불러오지 못했어요/)).toBeTruthy()
    expect(getByText('삼성전자')).toBeTruthy()

    await act(() => vi.advanceTimersByTimeAsync(3000))
    expect(queryByText(/최신 시세를 불러오지 못했어요/)).toBeNull()
  })

  it('탭이 백그라운드로 가면 폴링을 멈추고, 다시 보이면 즉시 재개한다', async () => {
    mockGetPrice.mockResolvedValue(price())

    renderCompanyPage()
    await act(() => vi.advanceTimersByTimeAsync(0))
    expect(mockGetPrice).toHaveBeenCalledTimes(1)

    await act(() => vi.advanceTimersByTimeAsync(3000))
    expect(mockGetPrice).toHaveBeenCalledTimes(2)

    // 탭이 백그라운드로 감: 이미 예약돼 있던 타이머는 그대로 한 번 더 울리지만(브라우저도 즉시 멈추지는
    // 않는다), 그 폴링이 끝나면 숨겨진 동안은 다음 폴링을 더 예약하지 않는다.
    Object.defineProperty(document, 'visibilityState', { value: 'hidden', configurable: true })
    document.dispatchEvent(new Event('visibilitychange'))

    await act(() => vi.advanceTimersByTimeAsync(3000))
    expect(mockGetPrice).toHaveBeenCalledTimes(3)

    // 숨겨진 동안 아무리 시간이 지나도 더 호출되지 않는다.
    await act(() => vi.advanceTimersByTimeAsync(30_000))
    expect(mockGetPrice).toHaveBeenCalledTimes(3)

    // 다시 보이면 타이머를 기다리지 않고 바로 한 번 더 불러온다.
    Object.defineProperty(document, 'visibilityState', { value: 'visible', configurable: true })
    await act(async () => {
      document.dispatchEvent(new Event('visibilitychange'))
      await vi.advanceTimersByTimeAsync(0)
    })
    expect(mockGetPrice).toHaveBeenCalledTimes(4)
  })
})
