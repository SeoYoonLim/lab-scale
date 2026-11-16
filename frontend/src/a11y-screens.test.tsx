import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { ReactElement } from 'react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from './api/errors'
import { translate } from './i18n'
import { LocaleContext } from './i18n/localeContext'
import BriefingPage from './pages/BriefingPage'
import SignupPage from './pages/SignupPage'
import DiagnosisHistorySection from './pages/portfolio/DiagnosisHistorySection'
import DiagnosisSection from './pages/portfolio/DiagnosisSection'
import HoldingsSection from './pages/portfolio/HoldingsSection'
import WatchlistSection from './pages/portfolio/WatchlistSection'
import { formatDate } from './utils/format'

// 포트폴리오·브리핑·가입 화면의 접근성 보강(결과 알림, 처리 중에도 초점 유지, 줄마다 구분되는 버튼 이름, 규칙 안내)을 확인한다.
vi.mock('./api', () => ({
  api: {
    placeOrder: vi.fn(),
    resetPortfolio: vi.fn(),
    getRealtimePrice: vi.fn(),
    listWatchlist: vi.fn(),
    addWatchlistItem: vi.fn(),
    removeWatchlistItem: vi.fn(),
    getDiagnosis: vi.fn(),
    compareDiagnosis: vi.fn(),
    getDiagnosisHistory: vi.fn(),
    getDiagnosisDetail: vi.fn(),
    deleteDiagnosis: vi.fn(),
    getCurrentDailyBriefing: vi.fn(),
    generateDailyBriefing: vi.fn(),
    listDailyBriefings: vi.fn(),
    getDailyBriefing: vi.fn(),
  },
}))
vi.mock('./auth/useAuth', () => ({ useAuth: () => ({ signup: vi.fn(), login: vi.fn(), notice: null }) }))
// 결과 본문 컴포넌트는 이 테스트와 무관하다
vi.mock('./components/DiagnosisResultView', () => ({ default: () => null }))
vi.mock('./components/DiagnosisCompareView', () => ({ default: () => null }))
vi.mock('./components/briefing/BriefingView', () => ({ default: () => null }))
const { api } = await import('./api')
const fn = (f: unknown) => f as ReturnType<typeof vi.fn>
const placeOrder = fn(api.placeOrder)
const getRealtimePrice = fn(api.getRealtimePrice)
const listWatchlist = fn(api.listWatchlist)
const addWatchlistItem = fn(api.addWatchlistItem)
const removeWatchlistItem = fn(api.removeWatchlistItem)
const getDiagnosis = fn(api.getDiagnosis)
const getDiagnosisHistory = fn(api.getDiagnosisHistory)
const deleteDiagnosis = fn(api.deleteDiagnosis)
const getCurrentDailyBriefing = fn(api.getCurrentDailyBriefing)
const generateDailyBriefing = fn(api.generateDailyBriefing)

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

// 끝나는 시점을 테스트가 정하는 약속
function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((r) => {
    resolve = r
  })
  return { promise, resolve }
}

// 보이지 않는 알림 영역(role=status) 중 글이 들어 있는 것의 글
const statusTexts = () => screen.getAllByRole('status').map((el) => el.textContent ?? '')

beforeEach(() => {
  vi.clearAllMocks()
  Element.prototype.scrollIntoView = vi.fn()
  getRealtimePrice.mockRejectedValue(new ApiError('없음', 404))
})

describe('보유·주문', () => {
  const portfolio = { cash_balance: 10_000_000, total_eval_amount: 0, total_asset: 10_000_000, holdings: [], note: null }

  it('주문하는 동안 버튼은 aria-disabled로 초점을 유지하고, 체결되면 알림 영역에 알린다', async () => {
    const order = deferred<unknown>()
    placeOrder.mockReturnValue(order.promise)
    renderWith(<HoldingsSection portfolio={portfolio as never} />)

    fireEvent.change(screen.getByLabelText('주문할 종목'), { target: { value: '삼성전자' } })
    const submit = screen.getByRole('button', { name: '주문' })
    submit.focus()
    fireEvent.click(submit)

    await waitFor(() => expect(submit.getAttribute('aria-disabled')).toBe('true'))
    expect((submit as HTMLButtonElement).disabled).toBe(false)
    expect(document.activeElement).toBe(submit)

    await act(async () => {
      order.resolve({
        side: 'buy',
        company_name: '삼성전자',
        quantity: 1,
        price: 70000,
        cash_balance: 9_930_000,
        holding: { quantity: 1, avg_price: 70000 },
      })
    })
    await waitFor(() => expect(statusTexts().some((t) => t.includes('체결') && t.includes('삼성전자'))).toBe(true))
    expect(document.activeElement).toBe(submit)
  })
})

describe('관심종목', () => {
  const item = (ticker: string, name: string) => ({
    ticker,
    company_name: name,
    asset_type: 'stock',
    added_at: '2026-11-01T00:00:00+09:00',
    latest_close: 1000,
    change_pct: 1,
  })

  it('삭제 버튼은 줄마다 종목 이름이 붙어 구분되고, 삭제하면 알린다', async () => {
    listWatchlist.mockResolvedValueOnce([item('000001', '가나다'), item('000002', '라마바')]).mockResolvedValue([item('000002', '라마바')])
    removeWatchlistItem.mockResolvedValue(undefined)
    renderWith(<WatchlistSection />)

    expect(await screen.findByRole('button', { name: '삭제 (가나다)' })).toBeTruthy()
    expect(screen.getByRole('button', { name: '삭제 (라마바)' })).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: '삭제 (가나다)' }))
    await waitFor(() => expect(statusTexts().some((t) => t.includes('가나다') && t.includes('삭제'))).toBe(true))
  })

  it('추가하면 결과를 알림 영역에 알린다', async () => {
    listWatchlist.mockResolvedValue([])
    addWatchlistItem.mockResolvedValue(item('005930', '삼성전자'))
    renderWith(<WatchlistSection />)

    fireEvent.change(await screen.findByLabelText('관심종목에 추가할 종목'), { target: { value: '삼성전자' } })
    fireEvent.click(screen.getByRole('button', { name: '추가' }))
    await waitFor(() => expect(statusTexts().some((t) => t.includes('삼성전자') && t.includes('추가'))).toBe(true))
  })
})

describe('AI 진단', () => {
  it('진단 중에는 버튼이 초점을 유지하고 진행을, 끝나면 완료를 알린다', async () => {
    const run = deferred<unknown>()
    getDiagnosis.mockReturnValue(run.promise)
    renderWith(<DiagnosisSection />)

    const button = screen.getByRole('button', { name: 'AI 진단 받기' })
    button.focus()
    fireEvent.click(button)

    await waitFor(() => expect(button.getAttribute('aria-disabled')).toBe('true'))
    expect(document.activeElement).toBe(button)
    expect(statusTexts().some((t) => t.includes('분석하고 있어요'))).toBe(true)

    // 진행 중에 또 눌러도 요청을 다시 보내지 않는다
    fireEvent.click(button)
    expect(getDiagnosis).toHaveBeenCalledTimes(1)

    await act(async () => {
      run.resolve({ diagnosis_id: 7 })
    })
    await waitFor(() => expect(statusTexts().some((t) => t.includes('진단이 끝났어요'))).toBe(true))
  })
})

describe('진단 이력', () => {
  const rows = [
    { id: 1, created_at: '2026-11-10T09:00:00+09:00', source: 'llm', total_asset: 1000, holdings_count: 1, flags: [], summary_preview: '' },
    { id: 2, created_at: '2026-11-12T09:00:00+09:00', source: 'llm', total_asset: 2000, holdings_count: 2, flags: [], summary_preview: '' },
  ]

  it('줄마다 버튼 이름에 날짜가 붙고, 상세·비교 버튼은 펼침 상태를 알린다', async () => {
    getDiagnosisHistory.mockResolvedValue({ total: 2, limit: 10, offset: 0, items: rows })
    renderWith(<DiagnosisHistorySection />)

    const d1 = formatDate(rows[0].created_at)
    const d2 = formatDate(rows[1].created_at)
    const detail = await screen.findByRole('button', { name: `상세 (${d1})` })
    expect(screen.getByRole('button', { name: `상세 (${d2})` })).toBeTruthy()
    expect(screen.getByRole('button', { name: `삭제 (${d1})` })).toBeTruthy()
    expect(screen.getByRole('button', { name: `이전과 비교 (${d1})` })).toBeTruthy()

    expect(detail.getAttribute('aria-expanded')).toBe('false')
    fireEvent.click(detail)
    await waitFor(() => expect(screen.getByRole('button', { name: `상세 닫기 (${d1})` }).getAttribute('aria-expanded')).toBe('true'))
  })

  it('삭제에 실패하면 오류를 화면에 보여준다(예전에는 조용히 사라졌다)', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    getDiagnosisHistory.mockResolvedValue({ total: 2, limit: 10, offset: 0, items: rows })
    deleteDiagnosis.mockRejectedValue(new ApiError('서버에서 삭제하지 못했어요.', 500))
    renderWith(<DiagnosisHistorySection />)

    fireEvent.click(await screen.findByRole('button', { name: `삭제 (${formatDate(rows[0].created_at)})` }))
    expect((await screen.findByRole('alert')).textContent).toContain('서버에서 삭제하지 못했어요.')
  })

  it('삭제에 성공하면 알림 영역에 알린다', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    getDiagnosisHistory.mockResolvedValueOnce({ total: 2, limit: 10, offset: 0, items: rows }).mockResolvedValue({ total: 1, limit: 10, offset: 0, items: [rows[1]] })
    deleteDiagnosis.mockResolvedValue(undefined)
    renderWith(<DiagnosisHistorySection />)

    fireEvent.click(await screen.findByRole('button', { name: `삭제 (${formatDate(rows[0].created_at)})` }))
    await waitFor(() => expect(statusTexts().some((t) => t.includes('진단을 삭제했어요'))).toBe(true))
  })
})

describe('브리핑', () => {
  it('만드는 동안 버튼이 초점을 유지하고 진행을, 끝나면 완료를 알린다', async () => {
    getCurrentDailyBriefing.mockResolvedValue(null)
    const gen = deferred<unknown>()
    generateDailyBriefing.mockReturnValue(gen.promise)
    renderWith(<BriefingPage />)

    const button = await screen.findByRole('button', { name: locale.t('daily_briefing.generate') })
    button.focus()
    fireEvent.click(button)

    await waitFor(() => expect(button.getAttribute('aria-disabled')).toBe('true'))
    expect(document.activeElement).toBe(button)
    expect(statusTexts().some((t) => t.includes('5~15초'))).toBe(true)

    fireEvent.click(button)
    expect(generateDailyBriefing).toHaveBeenCalledTimes(1)

    await act(async () => {
      gen.resolve({
        content: { title: '오늘의 브리핑' },
        generated_at: '2026-11-16T09:00:00+09:00',
        data_as_of: '2026-11-13',
        version: 1,
        locale: 'ko-KR',
        local_date: '2026-11-16',
        original_content: null,
      })
    })
    await waitFor(() => expect(statusTexts().some((t) => t.includes('브리핑을 만들었어요'))).toBe(true))
  })
})

describe('가입 화면', () => {
  it('아이디·비밀번호 규칙이 placeholder가 아니라 화면에 남는 글이고, 입력칸과 연결돼 있다', () => {
    renderWith(<SignupPage />)
    for (const [label, rule] of [
      ['아이디', '영문 소문자'],
      ['비밀번호', '8~128자'],
    ] as const) {
      const input = screen.getByLabelText(label)
      expect(input.getAttribute('placeholder')).toBeNull()
      const help = document.getElementById(input.getAttribute('aria-describedby') ?? '')
      expect(help?.textContent).toContain(rule)
    }
  })
})
