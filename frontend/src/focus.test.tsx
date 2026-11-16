import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { ReactElement } from 'react'
import { Link, MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import RouteA11y from './components/RouteA11y'
import { translate } from './i18n'
import { LocaleContext } from './i18n/localeContext'
import DeleteAccountCard from './pages/settings/DeleteAccountCard'
import ResearchPage from './pages/ResearchPage'
import WatchlistSection from './pages/portfolio/WatchlistSection'

// 화면이 바뀌거나 누른 버튼이 사라질 때 초점이 문서 맨 위로 날아가지 않고, 키보드 사용자가 이어서 쓸 곳에 남는지 확인한다.
vi.mock('./api', () => ({
  api: { listWatchlist: vi.fn(), removeWatchlistItem: vi.fn(), addWatchlistItem: vi.fn(), askResearch: vi.fn(), deleteAccount: vi.fn() },
}))
vi.mock('./auth/useAuth', () => ({ useAuth: () => ({ endSession: vi.fn() }) }))
// 리서치 화면의 데이터 카드들은 이 테스트와 무관하므로 비운다
vi.mock('./components/MarketSummaryCard', () => ({ default: () => null }))
vi.mock('./components/DataToolCards', () => ({ default: () => null }))
vi.mock('./components/TrendingWidget', () => ({ default: () => null }))
vi.mock('./components/ResearchAnswerBody', () => ({ default: () => null }))
vi.mock('./components/SourceFootnotes', () => ({ default: () => null }))
const { api } = await import('./api')
const listWatchlist = api.listWatchlist as unknown as ReturnType<typeof vi.fn>
const removeWatchlistItem = api.removeWatchlistItem as unknown as ReturnType<typeof vi.fn>
const askResearch = api.askResearch as unknown as ReturnType<typeof vi.fn>

const locale = {
  locale: 'ko-KR' as const,
  timezone: 'Asia/Seoul',
  preferences: undefined,
  t: (key: string, options?: { fallback?: string; vars?: Record<string, string | number> }) => translate('ko-KR', key, options),
}
const brand = locale.t('layout.brand_name', { fallback: '투자 리서치 에이전트' })

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
  document.title = ''
  // jsdom에는 scrollIntoView가 없다(리서치 화면이 새 답변으로 스크롤할 때 부른다)
  Element.prototype.scrollIntoView = vi.fn()
})

describe('화면 이동 시 제목과 초점 (RouteA11y)', () => {
  function App() {
    return (
      <>
        <RouteA11y />
        <nav>
          <Link to="/companies">종목으로</Link>
          <Link to="/portfolio">포트폴리오로</Link>
          <Link to="/portfolio/trades">체결로</Link>
        </nav>
        <Routes>
          <Route path="/" element={<h1>안녕하세요!</h1>} />
          <Route path="/companies" element={<h1>종목</h1>} />
          <Route
            path="/portfolio/*"
            element={
              <div>
                <h1>포트폴리오</h1>
              </div>
            }
          />
        </Routes>
      </>
    )
  }

  it('탭 제목을 화면 제목(h1)으로 바꾼다. 처음 열 때는 초점을 옮기지 않는다', () => {
    renderWith(<App />, ['/companies'])
    expect(document.title).toBe(`종목 · ${brand}`)
    expect(document.activeElement).toBe(document.body)
  })

  it('리서치 화면은 인사말 h1 대신 메뉴 이름을 제목으로 쓴다', () => {
    renderWith(<App />, ['/'])
    expect(document.title).toBe(`${locale.t('nav.research')} · ${brand}`)
  })

  it('다른 구역으로 이동하면 새 화면의 h1로 초점을 옮기고 제목을 바꾼다', async () => {
    renderWith(<App />, ['/companies'])
    fireEvent.click(screen.getByText('포트폴리오로'))
    const h1 = await screen.findByRole('heading', { level: 1, name: '포트폴리오' })
    await waitFor(() => expect(document.activeElement).toBe(h1))
    expect(document.title).toBe(`포트폴리오 · ${brand}`)
  })

  it('같은 구역 안의 이동(포트폴리오 탭 등)은 누르던 링크에서 초점을 빼앗지 않는다', async () => {
    renderWith(<App />, ['/portfolio'])
    const link = screen.getByText('체결로')
    link.focus()
    fireEvent.click(link)
    await act(async () => {})
    expect(document.activeElement).toBe(link)
  })
})

describe('관심종목 삭제 후 초점', () => {
  const item = (ticker: string, name: string) => ({
    ticker,
    company_name: name,
    asset_type: 'stock',
    added_at: '2026-11-01T00:00:00+09:00',
    latest_close: 1000,
    change_pct: 1,
  })

  it('삭제한 줄 대신 다음 줄의 삭제 버튼으로 초점이 간다', async () => {
    listWatchlist.mockResolvedValueOnce([item('000001', '가나다'), item('000002', '라마바')]).mockResolvedValue([item('000002', '라마바')])
    removeWatchlistItem.mockResolvedValue(undefined)
    renderWith(<WatchlistSection />)
    const buttons = await screen.findAllByRole('button', { name: '삭제' })
    expect(buttons).toHaveLength(2)
    buttons[0].focus()
    fireEvent.click(buttons[0])
    await waitFor(() => {
      const remaining = screen.getAllByRole('button', { name: '삭제' })
      expect(remaining).toHaveLength(1)
      expect(document.activeElement).toBe(remaining[0])
    })
  })

  it('마지막 한 줄을 지우면 종목 입력칸으로 초점이 간다', async () => {
    listWatchlist.mockResolvedValueOnce([item('000001', '가나다')]).mockResolvedValue([])
    removeWatchlistItem.mockResolvedValue(undefined)
    renderWith(<WatchlistSection />)
    const button = await screen.findByRole('button', { name: '삭제' })
    button.focus()
    fireEvent.click(button)
    await waitFor(() => expect(document.activeElement).toBe(screen.getByRole('textbox')))
  })
})

describe('회원 탈퇴 카드 펼침/접기 후 초점', () => {
  it('펼치면 비밀번호 칸으로, 취소하면 다시 "탈퇴 진행" 버튼으로 초점이 간다', async () => {
    renderWith(<DeleteAccountCard />)
    const open = screen.getByRole('button', { name: /회원 탈퇴 진행/ })
    expect(document.activeElement).not.toBe(open) // 처음 열릴 때 저절로 옮기지 않는다
    open.focus()
    fireEvent.click(open)
    const password = document.querySelector('input[type="password"]') as HTMLInputElement
    await waitFor(() => expect(document.activeElement).toBe(password))
    fireEvent.click(screen.getByRole('button', { name: '취소' }))
    await waitFor(() => expect(document.activeElement).toBe(screen.getByRole('button', { name: /회원 탈퇴 진행/ })))
  })
})

describe('리서치 화면의 입력창 초점', () => {
  it('첫 질문을 보내 입력창이 아래로 옮겨져도 초점이 입력창에 남는다', async () => {
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
    const first = screen.getByRole('textbox', { name: '질문 입력' })
    first.focus()
    fireEvent.change(first, { target: { value: '삼성전자 알려줘' } })
    fireEvent.keyDown(first, { key: 'Enter' })
    await waitFor(() => expect(askResearch).toHaveBeenCalled())
    await waitFor(() => {
      const textbox = screen.getByRole('textbox', { name: '질문 입력' })
      expect(document.activeElement).toBe(textbox)
    })
  })
})
