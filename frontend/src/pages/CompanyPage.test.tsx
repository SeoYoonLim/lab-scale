import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
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
  // retry:false - 안 그러면 실패한 폴링이 react-query 자체 재시도(기본 3회, 지수 backoff)까지 돌아서
  // 호출 횟수 단언이 어긋난다. CompanyPage.tsx 자체도 이 쿼리에 retry:false를 주지만, 테스트용
  // QueryClient는 따로 만들었으니 여기서도 같이 맞춰준다.
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[`/companies/${ticker}`]}>
        <Routes>
          <Route path="/companies/:ticker" element={<CompanyPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('CompanyPage 실시간 시세 폴링 (react-query의 refetchInterval에 위임)', () => {
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
    await act(() => vi.advanceTimersByTimeAsync(0))
    expect(mockGetPrice).toHaveBeenCalledTimes(1)

    // refetchInterval은 쿼리 함수가 끝난 뒤부터 다음 주기를 재므로, 첫 요청이 안 끝난 이상 몇 초가
    // 지나도 두 번째 요청이 나가면 안 된다(setInterval이었다면 9초 동안 2~3번 더 나갔을 것).
    await act(() => vi.advanceTimersByTimeAsync(9000))
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

    const { getByText, queryByText } = renderCompanyPage()
    await act(() => vi.advanceTimersByTimeAsync(10))
    expect(getByText('삼성전자')).toBeTruthy()

    await act(() => vi.advanceTimersByTimeAsync(3000))
    // 실패했어도 이전 성공 응답(삼성전자/가격)이 화면에서 사라지지 않는다 - react-query가 실패한
    // 리페치에도 마지막 성공 data는 그대로 둔다.
    expect(getByText(/최신 시세를 불러오지 못했어요/)).toBeTruthy()
    expect(getByText('삼성전자')).toBeTruthy()

    await act(() => vi.advanceTimersByTimeAsync(3000))
    expect(queryByText(/최신 시세를 불러오지 못했어요/)).toBeNull()
  })

  it('탭이 백그라운드로 가면 폴링을 멈추고, 다시 보이면 즉시 재개한다(react-query 기본 동작)', async () => {
    mockGetPrice.mockResolvedValue(price())

    renderCompanyPage()
    await act(() => vi.advanceTimersByTimeAsync(0))
    expect(mockGetPrice).toHaveBeenCalledTimes(1)

    await act(() => vi.advanceTimersByTimeAsync(3000))
    expect(mockGetPrice).toHaveBeenCalledTimes(2)

    // 탭이 백그라운드로 감: react-query의 refetchIntervalInBackground가 기본 false라 안 보이는 동안은
    // 폴링 자체를 멈춘다.
    Object.defineProperty(document, 'visibilityState', { value: 'hidden', configurable: true })
    window.dispatchEvent(new Event('visibilitychange'))

    const callsRightAfterHiding = mockGetPrice.mock.calls.length
    await act(() => vi.advanceTimersByTimeAsync(30_000))
    expect(mockGetPrice.mock.calls.length).toBe(callsRightAfterHiding)

    // 다시 보이면 react-query가 즉시(포커스 복귀) 한 번 더 불러오고 폴링을 재개한다.
    Object.defineProperty(document, 'visibilityState', { value: 'visible', configurable: true })
    await act(async () => {
      window.dispatchEvent(new Event('visibilitychange'))
      await vi.advanceTimersByTimeAsync(0)
    })
    expect(mockGetPrice.mock.calls.length).toBeGreaterThan(callsRightAfterHiding)
  })
})
