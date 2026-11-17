import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import type { ReactElement } from 'react'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { translate } from './i18n'
import { LocaleContext } from './i18n/localeContext'
import CompanyPage from './pages/CompanyPage'
import HoldingsSection from './pages/portfolio/HoldingsSection'

// 주문 빠른 선택(관심종목·오늘의 관심 종목·보유 종목 칩, 전량 매도)과 종목 상세의 "이 종목 주문하기"를 확인한다.
vi.mock('./api', () => ({
  USE_MOCK: false,
  api: {
    listWatchlist: vi.fn(),
    getTrending: vi.fn(),
    getRealtimePrice: vi.fn(),
    getPriceHistory: vi.fn(),
    addWatchlistItem: vi.fn(),
    placeOrder: vi.fn(),
    resetPortfolio: vi.fn(),
  },
}))
vi.mock('./auth/useAuth', () => ({ useAuth: () => ({ user: { username: 'tester' } }) }))
// 종목 상세의 데이터 카드들은 이 테스트와 무관하다
vi.mock('./components/PriceChartCard', () => ({ default: () => null }))
vi.mock('./components/EtfHoldingsCard', () => ({ default: () => null }))
vi.mock('./components/EventReactionsCard', () => ({ default: () => null }))
vi.mock('./components/StockNewsCard', () => ({ default: () => null }))
vi.mock('./components/StockDisclosuresCard', () => ({ default: () => null }))
const { api } = await import('./api')
const fn = (f: unknown) => f as ReturnType<typeof vi.fn>
const listWatchlist = fn(api.listWatchlist)
const getTrending = fn(api.getTrending)
const getRealtimePrice = fn(api.getRealtimePrice)
const getPriceHistory = fn(api.getPriceHistory)

const locale = {
  locale: 'ko-KR' as const,
  timezone: 'Asia/Seoul',
  preferences: undefined,
  t: (key: string, options?: { fallback?: string; vars?: Record<string, string | number> }) => translate('ko-KR', key, options),
}

function renderWith(ui: ReactElement, entries: string[] = ['/']) {
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <LocaleContext.Provider value={locale}>
        <MemoryRouter initialEntries={entries}>{ui}</MemoryRouter>
      </LocaleContext.Provider>
    </QueryClientProvider>,
  )
}

const statusTexts = () => screen.getAllByRole('status').map((el) => el.textContent ?? '')

const holding = (ticker: string, name: string, quantity: number) => ({
  ticker,
  company_name: name,
  asset_type: 'stock',
  quantity,
  avg_price: 1000,
  current_price: 1100,
  is_realtime: true,
  eval_amount: 1100 * quantity,
  profit_loss: 100,
  profit_loss_pct: 10,
})
const portfolio = {
  cash_balance: 1_000_000,
  total_eval_amount: 0,
  total_asset: 1_000_000,
  note: null,
  holdings: [holding('005930', '삼성전자', 12), holding('000660', 'SK하이닉스', 3)],
}

beforeEach(() => {
  vi.clearAllMocks()
  Element.prototype.scrollIntoView = vi.fn()
  getRealtimePrice.mockRejectedValue(new Error('없음'))
  listWatchlist.mockResolvedValue([
    { ticker: '035420', company_name: 'NAVER', asset_type: 'stock', added_at: '2026-11-01T00:00:00+09:00', latest_close: 1, change_pct: 0 },
  ])
  getTrending.mockResolvedValue({
    category: 'gainers',
    asset_type: 'stock',
    price_date: '2026-11-13',
    window_days: 1,
    items: [{ ticker: '373220', company_name: 'LG에너지솔루션', close_price: 1, change_pct: 5, volume: 1, avg_volume: 1, volume_ratio: null }],
  })
})

describe('주문 빠른 선택', () => {
  it('매수에서는 관심종목과 오늘의 관심 종목 칩이 보이고, 누르면 주문 종목 칸이 채워진다', async () => {
    renderWith(<HoldingsSection portfolio={portfolio as never} />)
    const watch = await screen.findByRole('button', { name: 'NAVER' })
    expect(screen.getByRole('button', { name: 'LG에너지솔루션' })).toBeTruthy()
    // 보유 종목 칩은 매도일 때만 보인다
    expect(screen.queryByRole('button', { name: /삼성전자/ })).toBeNull()

    watch.focus()
    fireEvent.click(watch)
    expect((screen.getByLabelText('주문할 종목') as HTMLInputElement).value).toBe('035420')
    // 눌러도 칩의 초점이 유지되고, 선택 상태가 알려지고, 채워진 것이 소리로도 알려진다
    expect(document.activeElement).toBe(watch)
    expect(watch.getAttribute('aria-pressed')).toBe('true')
    expect(screen.getByRole('button', { name: 'LG에너지솔루션' }).getAttribute('aria-pressed')).toBe('false')
    expect(statusTexts().some((t) => t.includes('NAVER') && t.includes('선택'))).toBe(true)
  })

  it('매도로 바꾸면 보유 종목 칩(수량 포함)만 보이고, 고르면 "전량 매도"로 수량을 채운다', async () => {
    renderWith(<HoldingsSection portfolio={portfolio as never} />)
    await screen.findByRole('button', { name: 'NAVER' })
    fireEvent.change(screen.getByLabelText('매수/매도'), { target: { value: 'sell' } })

    // 매도 중에는 관심종목·오늘의 관심 종목 칩이 사라진다
    expect(screen.queryByRole('button', { name: 'NAVER' })).toBeNull()
    const group = screen.getByRole('group', { name: '보유 종목' })
    const samsung = within(group).getByRole('button', { name: /삼성전자/ })
    expect(samsung.textContent).toContain('12주')
    expect(within(group).getByRole('button', { name: /SK하이닉스/ }).textContent).toContain('3주')

    fireEvent.click(samsung)
    expect((screen.getByLabelText('주문할 종목') as HTMLInputElement).value).toBe('005930')
    expect(screen.getByText(/삼성전자 12주를 가지고 있어요/)).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: '전량 매도(12주)' }))
    expect((screen.getByLabelText('수량') as HTMLInputElement).value).toBe('12')
    expect(statusTexts().some((t) => t.includes('12주') && t.includes('전량'))).toBe(true)
  })

  it('보유 종목이 없으면 매도에서 안내를 보여준다', async () => {
    renderWith(<HoldingsSection portfolio={{ ...portfolio, holdings: [] } as never} />)
    await screen.findByRole('button', { name: 'NAVER' })
    fireEvent.change(screen.getByLabelText('매수/매도'), { target: { value: 'sell' } })
    expect(screen.getByText('아직 팔 수 있는 보유 종목이 없어요.')).toBeTruthy()
  })

  it('관심종목·인기 종목이 모두 비어 있으면 칩 영역을 그리지 않는다', async () => {
    listWatchlist.mockResolvedValue([])
    getTrending.mockResolvedValue({ category: 'gainers', asset_type: 'stock', price_date: '2026-11-13', window_days: 1, items: [] })
    const { container } = renderWith(<HoldingsSection portfolio={portfolio as never} />)
    await waitFor(() => expect(listWatchlist).toHaveBeenCalled())
    expect(container.querySelector('.order-picks')).toBeNull()
  })

  it('매도 중에는 관심종목·인기 종목을 새로 요청하지 않는다', async () => {
    renderWith(<HoldingsSection portfolio={portfolio as never} />)
    await screen.findByRole('button', { name: 'NAVER' })
    listWatchlist.mockClear()
    getTrending.mockClear()
    fireEvent.change(screen.getByLabelText('매수/매도'), { target: { value: 'sell' } })
    expect(listWatchlist).not.toHaveBeenCalled()
    expect(getTrending).not.toHaveBeenCalled()
  })
})

describe('종목 상세에서 바로 주문', () => {
  function PortfolioStub() {
    const state = useLocation().state as { orderTicker?: string; orderName?: string } | null
    return <p data-testid="order-state">{state ? `${state.orderTicker}|${state.orderName}` : 'none'}</p>
  }

  it('"이 종목 주문하기"를 누르면 포트폴리오로 종목 코드와 이름을 넘긴다', async () => {
    getRealtimePrice.mockResolvedValue({
      ticker: '005930',
      company_name: '삼성전자',
      current_price: 70000,
      change_amount: 100,
      change_pct: 0.1,
      as_of: '2026-11-13',
      queried_at: '2026-11-13T10:00:00+09:00',
      is_realtime: false,
      source: 'fallback',
      corrected_from: null,
    })
    getPriceHistory.mockResolvedValue({ asset_type: 'stock', items: [], count: 0, days: 60, company_name: '삼성전자' })
    renderWith(
      <Routes>
        <Route path="/companies/:ticker" element={<CompanyPage />} />
        <Route path="/portfolio" element={<PortfolioStub />} />
      </Routes>,
      ['/companies/005930'],
    )
    // 시세가 와서 종목 이름이 보인 뒤에 누른다(그 전에 누르면 이름 자리에 종목 코드가 들어간다)
    await screen.findByRole('heading', { name: /삼성전자/ })
    fireEvent.click(screen.getByRole('button', { name: '이 종목 주문하기' }))
    expect((await screen.findByTestId('order-state')).textContent).toBe('005930|삼성전자')
  })

  it('넘어온 종목이 있으면 주문 칸을 채우고 수량 칸으로 초점을 옮기며, 한 번 쓰면 state를 비운다', async () => {
    function LocationProbe() {
      return <span data-testid="state-now">{JSON.stringify(useLocation().state)}</span>
    }
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <LocaleContext.Provider value={locale}>
          <MemoryRouter initialEntries={[{ pathname: '/portfolio', state: { orderTicker: '005930', orderName: '삼성전자' } }]}>
            <HoldingsSection portfolio={portfolio as never} />
            <LocationProbe />
          </MemoryRouter>
        </LocaleContext.Provider>
      </QueryClientProvider>,
    )
    await waitFor(() => expect((screen.getByLabelText('주문할 종목') as HTMLInputElement).value).toBe('005930'))
    expect(document.activeElement).toBe(screen.getByLabelText('수량'))
    expect(statusTexts().some((t) => t.includes('삼성전자') && t.includes('채웠어요'))).toBe(true)
    // 뒤로/앞으로가기로 다시 채워지지 않게 state를 비운다
    await waitFor(() => expect(screen.getByTestId('state-now').textContent).toBe('null'))
  })
})
