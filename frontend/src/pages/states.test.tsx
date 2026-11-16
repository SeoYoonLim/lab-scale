import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { Suspense, lazy, type ReactElement } from 'react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '../api/errors'
import ErrorBoundary from '../components/ui/ErrorBoundary'
import { translate } from '../i18n'
import { LocaleContext } from '../i18n/localeContext'
import CompanyListPage from './CompanyListPage'
import TradesSection from './portfolio/TradesSection'
import WatchlistSection from './portfolio/WatchlistSection'

// 화면마다 "불러오는 중 / 데이터 없음 / 오류" 세 상태를 같은 방식으로 처리하는지 확인한다.
// 로딩은 role=status(화면 읽기 프로그램이 읽음), 오류는 role=alert, 데이터 없음은 오류가 아니라 안내 문장이다.
vi.mock('../api', () => ({
  api: { listWatchlist: vi.fn(), getTrades: vi.fn(), searchCompanies: vi.fn() },
}))
const { api } = await import('../api')
const listWatchlist = api.listWatchlist as unknown as ReturnType<typeof vi.fn>
const getTrades = api.getTrades as unknown as ReturnType<typeof vi.fn>
const searchCompanies = api.searchCompanies as unknown as ReturnType<typeof vi.fn>

const locale = {
  locale: 'ko-KR' as const,
  timezone: 'Asia/Seoul',
  preferences: undefined,
  t: (key: string, options?: { fallback?: string; vars?: Record<string, string | number> }) => translate('ko-KR', key, options),
}

function renderWith(ui: ReactElement) {
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <LocaleContext.Provider value={locale}>
        <MemoryRouter>{ui}</MemoryRouter>
      </LocaleContext.Provider>
    </QueryClientProvider>,
  )
}

const never = () => new Promise(() => {})
const serverError = () => new ApiError('서버에서 문제가 생겼어요.', 500)

const screens: { name: string; ui: () => ReactElement; mock: ReturnType<typeof vi.fn>; empty: unknown; emptyText: string }[] = [
  { name: '관심종목', ui: () => <WatchlistSection />, mock: listWatchlist, empty: [], emptyText: '아직 등록한 관심종목이 없어요.' },
  { name: '체결 내역', ui: () => <TradesSection />, mock: getTrades, empty: { items: [], total: 0, limit: 20, offset: 0 }, emptyText: '체결 내역이 없어요.' },
  { name: '종목 검색', ui: () => <CompanyListPage />, mock: searchCompanies, empty: [], emptyText: '검색 결과가 없어요.' },
]

beforeEach(() => vi.clearAllMocks())

describe.each(screens)('$name 화면의 세 상태', ({ ui, mock, empty, emptyText }) => {
  it('불러오는 중에는 role=status로 알린다', async () => {
    mock.mockImplementation(never)
    renderWith(ui())
    // 알림 전용(보이지 않는) 영역이 같이 있을 수 있어서, 상태 영역 중 하나가 "불러오는 중"이면 된다
    const statuses = await screen.findAllByRole('status')
    expect(statuses.some((el) => (el.textContent ?? '').includes('불러'))).toBe(true)
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('데이터가 없으면 오류가 아니라 안내 문장을 보여준다', async () => {
    mock.mockResolvedValue(empty)
    renderWith(ui())
    expect(await screen.findByText(emptyText)).toBeTruthy()
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('API 오류면 role=alert로 사유를 알리고, 서버 오류는 다시 시도 버튼을 준다', async () => {
    mock.mockRejectedValueOnce(serverError())
    renderWith(ui())
    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toContain('서버에서 문제가 생겼어요.')
    mock.mockResolvedValue(empty)
    fireEvent.click(screen.getByRole('button', { name: '다시 시도' }))
    expect(await screen.findByText(emptyText)).toBeTruthy()
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('네트워크가 끊기면(fetch TypeError) 화면 언어로 안내한다', async () => {
    mock.mockRejectedValueOnce(new TypeError('Failed to fetch'))
    renderWith(ui())
    const alert = await screen.findByRole('alert')
    expect(alert.textContent).not.toContain('Failed to fetch')
  })
})

describe('ErrorBoundary', () => {
  let consoleError: ReturnType<typeof vi.spyOn>
  beforeEach(() => {
    consoleError = vi.spyOn(console, 'error').mockImplementation(() => {})
  })
  afterEach(() => consoleError.mockRestore())

  function Boom(): ReactElement {
    throw new Error('render crash')
  }

  it('렌더 중 예외가 나면 흰 화면 대신 안내와 새로고침·처음 화면 버튼을 보여준다', () => {
    renderWith(
      <ErrorBoundary>
        <Boom />
      </ErrorBoundary>,
    )
    expect(screen.getByRole('alert').textContent).toContain('화면을 표시하지 못했어요')
    expect(screen.getByRole('button', { name: '새로고침' })).toBeTruthy()
    expect(screen.getByRole('link', { name: '처음 화면으로' }).getAttribute('href')).toBe('/')
  })

  it('화면 코드 조각(lazy 청크)을 못 받아도 같은 안내가 뜬다', async () => {
    const Broken = lazy<() => ReactElement>(() => Promise.reject(new Error('Failed to fetch dynamically imported module')))
    renderWith(
      <ErrorBoundary>
        <Suspense fallback={<p>loading</p>}>
          <Broken />
        </Suspense>
      </ErrorBoundary>,
    )
    await waitFor(() => expect(screen.getByRole('alert').textContent).toContain('화면을 표시하지 못했어요'))
  })

  it('경로가 바뀌면(resetKey) 오류 상태가 풀리고 새 화면을 보여준다', () => {
    let crash = true
    function Maybe(): ReactElement {
      if (crash) throw new Error('crash')
      return <p>정상 화면</p>
    }
    const wrap = (key: string) => (
      <QueryClientProvider client={new QueryClient()}>
        <LocaleContext.Provider value={locale}>
          <MemoryRouter>
            <ErrorBoundary resetKey={key}>
              <Maybe />
            </ErrorBoundary>
          </MemoryRouter>
        </LocaleContext.Provider>
      </QueryClientProvider>
    )
    const { rerender } = render(wrap('/a'))
    expect(screen.getByRole('alert')).toBeTruthy()
    crash = false
    rerender(wrap('/b'))
    expect(screen.getByText('정상 화면')).toBeTruthy()
    expect(screen.queryByRole('alert')).toBeNull()
  })
})
