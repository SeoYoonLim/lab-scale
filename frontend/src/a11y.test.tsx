import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { ReactElement } from 'react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from './api/errors'
import { translate } from './i18n'
import { LocaleContext } from './i18n/localeContext'
import Pagination from './components/Pagination'
import SourceFootnotes from './components/SourceFootnotes'
import { FeedFilters } from './components/stockFeed/FeedControls'
import { DEFAULT_FILTERS } from './components/stockFeed/filters'
import CompanyPage from './pages/CompanyPage'
import LoginPage from './pages/LoginPage'
import ResearchPage from './pages/ResearchPage'

// 접근성 보강(키보드 초점 유지, 화면 읽기 프로그램 알림, 이름·상태 표시)이 계속 지켜지는지 확인한다.
const { loginMock } = vi.hoisted(() => ({ loginMock: vi.fn() }))
vi.mock('./api', () => ({
  api: {
    askResearch: vi.fn(),
    getTrending: vi.fn(),
    getRealtimePrice: vi.fn(),
    getPriceHistory: vi.fn(),
    addWatchlistItem: vi.fn(),
  },
}))
vi.mock('./auth/useAuth', () => ({
  useAuth: () => ({ user: { username: 'tester' }, login: loginMock, notice: null }),
}))
// 이 테스트와 무관한 데이터 카드는 비운다
vi.mock('./components/MarketSummaryCard', () => ({ default: () => null }))
vi.mock('./components/DataToolCards', () => ({ default: () => null }))
vi.mock('./components/ResearchAnswerBody', () => ({ default: () => null }))
vi.mock('./components/PriceChartCard', () => ({ default: () => null }))
vi.mock('./components/EtfHoldingsCard', () => ({ default: () => null }))
vi.mock('./components/EventReactionsCard', () => ({ default: () => null }))
vi.mock('./components/StockNewsCard', () => ({ default: () => null }))
vi.mock('./components/StockDisclosuresCard', () => ({ default: () => null }))
const { api } = await import('./api')
const askResearch = api.askResearch as unknown as ReturnType<typeof vi.fn>
const getTrending = api.getTrending as unknown as ReturnType<typeof vi.fn>
const getRealtimePrice = api.getRealtimePrice as unknown as ReturnType<typeof vi.fn>
const getPriceHistory = api.getPriceHistory as unknown as ReturnType<typeof vi.fn>
const addWatchlistItem = api.addWatchlistItem as unknown as ReturnType<typeof vi.fn>

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

beforeEach(() => {
  vi.clearAllMocks()
  Element.prototype.scrollIntoView = vi.fn()
})

describe('로그인 화면', () => {
  it('<main>과 "본문으로 건너뛰기" 링크가 있다', () => {
    renderWith(<LoginPage />, ['/login'])
    expect(screen.getByRole('main')).toHaveProperty('id', 'main')
    expect(screen.getByRole('link', { name: '본문으로 건너뛰기' }).getAttribute('href')).toBe('#main')
  })

  it('빈 칸으로 보내도 버튼은 꺼져 있지 않고, 안내와 함께 빈 칸으로 초점이 간다', () => {
    renderWith(<LoginPage />, ['/login'])
    const submit = screen.getByRole('button', { name: '로그인' })
    expect((submit as HTMLButtonElement).disabled).toBe(false)

    fireEvent.click(submit)

    expect(screen.getByRole('alert').textContent).toBe('아이디와 비밀번호를 입력해주세요.')
    const username = screen.getByLabelText('아이디')
    expect(document.activeElement).toBe(username)
    expect(username.getAttribute('aria-invalid')).toBe('true')
    expect(username.getAttribute('aria-describedby')).toBe(screen.getByRole('alert').id)
    expect(loginMock).not.toHaveBeenCalled()
  })

  it('아이디만 채우고 보내면 비밀번호 칸으로 초점이 간다', () => {
    renderWith(<LoginPage />, ['/login'])
    fireEvent.change(screen.getByLabelText('아이디'), { target: { value: 'dummy-user' } })
    fireEvent.click(screen.getByRole('button', { name: '로그인' }))
    expect(document.activeElement).toBe(screen.getByLabelText('비밀번호'))
    expect(screen.getByLabelText('비밀번호').getAttribute('aria-invalid')).toBe('true')
    expect(screen.getByLabelText('아이디').getAttribute('aria-invalid')).toBeNull()
  })
})

describe('페이지네이션', () => {
  it('끝에서 누른 버튼은 꺼지지 않고 초점을 유지하며, 더 이상 이동하지 않는다', () => {
    const onChange = vi.fn()
    const { rerender } = render(
      <LocaleContext.Provider value={locale}>
        <Pagination offset={0} pageSize={10} total={15} onChange={onChange} />
      </LocaleContext.Provider>,
    )
    const next = screen.getByRole('button', { name: '다음' })
    next.focus()
    fireEvent.click(next)
    expect(onChange).toHaveBeenLastCalledWith(10)

    // 마지막 쪽으로 넘어간 상태
    rerender(
      <LocaleContext.Provider value={locale}>
        <Pagination offset={10} pageSize={10} total={15} onChange={onChange} />
      </LocaleContext.Provider>,
    )
    expect(document.activeElement).toBe(next)
    expect(next.getAttribute('aria-disabled')).toBe('true')
    expect((next as HTMLButtonElement).disabled).toBe(false)
    fireEvent.click(next)
    expect(onChange).toHaveBeenCalledTimes(1)

    expect(screen.getByRole('button', { name: '이전' }).getAttribute('aria-disabled')).toBeNull()
  })

  it('첫 쪽의 "이전"은 aria-disabled이고 눌러도 이동하지 않는다', () => {
    const onChange = vi.fn()
    render(
      <LocaleContext.Provider value={locale}>
        <Pagination offset={0} pageSize={10} total={15} onChange={onChange} />
      </LocaleContext.Provider>,
    )
    const prev = screen.getByRole('button', { name: '이전' })
    expect(prev.getAttribute('aria-disabled')).toBe('true')
    fireEvent.click(prev)
    expect(onChange).not.toHaveBeenCalled()
  })
})

describe('뉴스·공시 필터', () => {
  it('정렬 select의 이름은 고른 옵션이 아니라 "정렬"이다', () => {
    const { rerender } = render(
      <LocaleContext.Provider value={locale}>
        <FeedFilters value={DEFAULT_FILTERS} onChange={vi.fn()} />
      </LocaleContext.Provider>,
    )
    expect(screen.getByRole('combobox', { name: '정렬' })).toBeTruthy()
    rerender(
      <LocaleContext.Provider value={locale}>
        <FeedFilters value={{ ...DEFAULT_FILTERS, order: 'asc' }} onChange={vi.fn()} />
      </LocaleContext.Provider>,
    )
    expect(screen.getByRole('combobox', { name: '정렬' })).toBeTruthy()
  })

  it('시작일이 종료일보다 늦으면 두 날짜 입력에 aria-invalid가 붙는다', () => {
    render(
      <LocaleContext.Provider value={locale}>
        <FeedFilters value={{ ...DEFAULT_FILTERS, dateFrom: '2026-11-10', dateTo: '2026-11-01' }} onChange={vi.fn()} />
      </LocaleContext.Provider>,
    )
    expect(screen.getByLabelText('시작일').getAttribute('aria-invalid')).toBe('true')
    expect(screen.getByLabelText('종료일').getAttribute('aria-invalid')).toBe('true')
  })
})

describe('새 탭 링크 안내', () => {
  it('근거 링크의 이름에 "새 탭에서 열림"이 들어 있다', () => {
    render(
      <LocaleContext.Provider value={locale}>
        <SourceFootnotes
          usedTools={['news_tool']}
          sources={[{ type: 'news', title: '삼성전자 기사', url: 'https://example.com/a' }] as never}
        />
      </LocaleContext.Provider>,
    )
    const link = screen.getByRole('link', { name: /삼성전자 기사/ })
    expect(link.textContent).toContain('새 탭에서 열림')
  })
})

describe('리서치 화면', () => {
  const trending = {
    category: 'gainers',
    asset_type: 'stock',
    price_date: '2026-11-13',
    window_days: 1,
    items: [{ ticker: '005930', company_name: '삼성전자', close_price: 70000, change_pct: 1.2, volume: 1, avg_volume: 1, volume_ratio: null }],
  }

  it('답변이 도착하면 알림 영역에 알리고, 첫 질문 뒤에도 h1이 남는다', async () => {
    getTrending.mockResolvedValue(trending)
    askResearch.mockResolvedValue({
      report_id: 1,
      previous_report_id: null,
      answer: '답',
      used_tools: [],
      sources: [],
      disclaimer: '',
      locale: 'ko-KR',
    })
    renderWith(<ResearchPage />)
    const input = screen.getByRole('textbox', { name: '질문 입력' })
    fireEvent.change(input, { target: { value: '삼성전자 알려줘' } })
    fireEvent.click(screen.getByRole('button', { name: '질문 보내기' }))

    await waitFor(() => expect(screen.getByText('분석 결과가 도착했어요.')).toBeTruthy())
    expect(screen.getByText('분석 결과가 도착했어요.').closest('[role="status"]')).not.toBeNull()
    expect(screen.getByRole('heading', { level: 1 })).toBeTruthy()
  })

  it('입력창 아래에 국내 상장 종목만 지원한다는 안내가 보인다', () => {
    renderWith(<ResearchPage />)
    expect(screen.getByText(/국내 상장 종목만 지원해요/)).toBeTruthy()
  })

  it('급등·급락 같은 분류 버튼이 선택 상태(aria-pressed)를 알린다', async () => {
    getTrending.mockResolvedValue(trending)
    renderWith(<ResearchPage />)
    const gainers = await screen.findByRole('button', { name: '급등' })
    expect(gainers.getAttribute('aria-pressed')).toBe('true')
    const losers = screen.getByRole('button', { name: '급락' })
    expect(losers.getAttribute('aria-pressed')).toBe('false')

    fireEvent.click(losers)
    expect(screen.getByRole('button', { name: '급락' }).getAttribute('aria-pressed')).toBe('true')
    expect(screen.getByRole('button', { name: '급등' }).getAttribute('aria-pressed')).toBe('false')
  })

  it('관심 종목을 누르면 질문이 채워지고 입력창으로 초점이 가며 알려준다', async () => {
    getTrending.mockResolvedValue(trending)
    renderWith(<ResearchPage />)
    fireEvent.click(await screen.findByRole('button', { name: /^삼성전자.*70,000/ }))

    const input = screen.getByRole('textbox', { name: '질문 입력' }) as HTMLTextAreaElement
    expect(input.value).toContain('삼성전자')
    expect(document.activeElement).toBe(input)
    expect(screen.getByText('질문이 입력창에 채워졌어요. 확인하고 보내주세요.').closest('[role="status"]')).not.toBeNull()
  })
})

describe('종목 상세 화면', () => {
  const realtime = {
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
  }

  function renderCompany() {
    return renderWith(
      <Routes>
        <Route path="/companies/:ticker" element={<CompanyPage />} />
      </Routes>,
      ['/companies/005930'],
    )
  }

  it('등록되지 않은 종목(404) 화면에도 h1이 있다', async () => {
    getRealtimePrice.mockRejectedValue(new ApiError('등록되지 않은 종목이에요.', 404))
    getPriceHistory.mockRejectedValue(new ApiError('등록되지 않은 종목이에요.', 404))
    renderCompany()
    await waitFor(() => expect(screen.getByRole('heading', { level: 1 }).textContent).toBe('등록되지 않은 종목이에요.'))
  })

  it('관심종목 추가 뒤에도 버튼이 초점을 유지하고 결과를 알린다', async () => {
    getRealtimePrice.mockResolvedValue(realtime)
    getPriceHistory.mockResolvedValue({ asset_type: 'stock', items: [], count: 0, days: 60, company_name: '삼성전자' })
    addWatchlistItem.mockResolvedValue({})
    renderCompany()

    const button = await screen.findByRole('button', { name: '관심종목 추가' })
    button.focus()
    fireEvent.click(button)

    await waitFor(() => expect(button.textContent).toBe('관심종목에 추가했어요'))
    expect(document.activeElement).toBe(button)
    expect(button.getAttribute('aria-disabled')).toBe('true')
    expect((button as HTMLButtonElement).disabled).toBe(false)
    const status = screen.getAllByRole('status').find((el) => el.textContent === '관심종목에 추가했어요')
    expect(status).toBeTruthy()

    // 다시 눌러도 중복 요청을 보내지 않는다
    fireEvent.click(button)
    expect(addWatchlistItem).toHaveBeenCalledTimes(1)
  })
})
