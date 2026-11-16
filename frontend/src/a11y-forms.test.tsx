import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { ReactElement } from 'react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import EtfHoldingsCard from './components/EtfHoldingsCard'
import ScrollRegion from './components/ui/ScrollRegion'
import { translate } from './i18n'
import { LocaleContext } from './i18n/localeContext'
import RecoveryRequestPage from './pages/RecoveryRequestPage'
import SettingsPage from './pages/SettingsPage'
import SignupPage from './pages/SignupPage'
import DeleteAccountCard from './pages/settings/DeleteAccountCard'
import EmailCard from './pages/settings/EmailCard'
import PasswordCard from './pages/settings/PasswordCard'
import HoldingsSection from './pages/portfolio/HoldingsSection'
import WatchlistSection from './pages/portfolio/WatchlistSection'

// 입력이 비었을 때 버튼을 끄는 대신(꺼진 버튼은 키보드로 닿지 않는다) 제출을 받아 안내하고 해당 칸으로 초점을 옮기는지,
// 가로 스크롤 표가 키보드 초점과 이름을 가지는지 확인한다. 테스트용 가짜 비밀번호에는 dummy를 넣는다(secret_scan).
const { signupMock, endSessionMock } = vi.hoisted(() => ({ signupMock: vi.fn(), endSessionMock: vi.fn() }))
vi.mock('./api', () => ({
  USE_MOCK: false,
  api: {
    me: vi.fn(),
    setEmail: vi.fn(),
    resendVerification: vi.fn(),
    changePassword: vi.fn(),
    deleteAccount: vi.fn(),
    forgotPassword: vi.fn(),
    findUsername: vi.fn(),
    getPreferences: vi.fn(),
    updatePreferences: vi.fn(),
    placeOrder: vi.fn(),
    getRealtimePrice: vi.fn(),
    listWatchlist: vi.fn(),
    addWatchlistItem: vi.fn(),
    getEtfHoldings: vi.fn(),
  },
}))
vi.mock('./auth/useAuth', () => ({
  useAuth: () => ({ signup: signupMock, login: vi.fn(), notice: null, endSession: endSessionMock }),
}))
const { api } = await import('./api')
const fn = (f: unknown) => f as ReturnType<typeof vi.fn>
const placeOrder = fn(api.placeOrder)
const getRealtimePrice = fn(api.getRealtimePrice)
const listWatchlist = fn(api.listWatchlist)
const addWatchlistItem = fn(api.addWatchlistItem)
const setEmail = fn(api.setEmail)
const me = fn(api.me)
const changePassword = fn(api.changePassword)
const deleteAccount = fn(api.deleteAccount)
const forgotPassword = fn(api.forgotPassword)
const getEtfHoldings = fn(api.getEtfHoldings)

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

beforeEach(() => {
  vi.clearAllMocks()
  getRealtimePrice.mockRejectedValue(new Error('없음'))
  me.mockResolvedValue({ email: null, email_verified: false })
})

describe('가입', () => {
  it('빈 칸으로 보내도 버튼은 꺼져 있지 않고, 안내와 함께 빈 칸으로 초점이 간다', () => {
    renderWith(<SignupPage />)
    const submit = screen.getByRole('button', { name: '회원가입' })
    expect((submit as HTMLButtonElement).disabled).toBe(false)

    fireEvent.click(submit)

    expect(screen.getByRole('alert').textContent).toBe('아이디와 비밀번호를 입력해주세요.')
    expect(document.activeElement).toBe(screen.getByLabelText('아이디'))
    expect(screen.getByLabelText('아이디').getAttribute('aria-invalid')).toBe('true')
    expect(signupMock).not.toHaveBeenCalled()
  })

  it('비밀번호가 너무 짧으면 비밀번호 칸으로 초점이 가고 보내지 않는다', () => {
    renderWith(<SignupPage />)
    fireEvent.change(screen.getByLabelText('아이디'), { target: { value: 'dummy_user' } })
    fireEvent.change(screen.getByLabelText('비밀번호'), { target: { value: 'dummy1' } })
    fireEvent.click(screen.getByRole('button', { name: '회원가입' }))

    expect(screen.getByRole('alert').textContent).toContain('8')
    expect(document.activeElement).toBe(screen.getByLabelText('비밀번호'))
    expect(signupMock).not.toHaveBeenCalled()
  })
})

describe('계정 복구 요청', () => {
  it('올바르지 않은 이메일이면 안내와 함께 이메일 칸으로 초점이 가고 보내지 않는다', () => {
    renderWith(<RecoveryRequestPage mode="password" />)
    const submit = screen.getByRole('button', { name: '재설정 링크 받기' })
    expect((submit as HTMLButtonElement).disabled).toBe(false)

    fireEvent.change(screen.getByLabelText('이메일'), { target: { value: 'not-an-email' } })
    fireEvent.click(submit)

    expect(screen.getByRole('alert').textContent).toBe('올바른 이메일 주소를 입력해주세요.')
    expect(document.activeElement).toBe(screen.getByLabelText('이메일'))
    expect(screen.getByLabelText('이메일').getAttribute('aria-invalid')).toBe('true')
    expect(forgotPassword).not.toHaveBeenCalled()
  })
})

describe('설정', () => {
  it('이메일: 이메일이 틀리면 이메일 칸, 비밀번호가 비면 비밀번호 칸으로 초점이 간다', async () => {
    renderWith(<EmailCard />)
    await screen.findByLabelText('이메일', { selector: 'input' })
    const submit = screen.getByRole('button', { name: '이메일 등록' })
    expect((submit as HTMLButtonElement).disabled).toBe(false)

    fireEvent.click(submit)
    expect(screen.getByRole('alert').textContent).toBe('올바른 이메일 주소를 입력해주세요.')
    expect(document.activeElement).toBe(screen.getByLabelText('이메일', { selector: 'input' }))

    fireEvent.change(screen.getByLabelText('이메일', { selector: 'input' }), { target: { value: 'dummy@example.com' } })
    fireEvent.click(submit)
    expect(screen.getByRole('alert').textContent).toBe('현재 비밀번호를 입력해주세요.')
    expect(document.activeElement).toBe(screen.getByLabelText(/현재 비밀번호/))
    expect(setEmail).not.toHaveBeenCalled()
  })

  it('비밀번호 변경: 빈 칸이 있으면 첫 빈 칸으로 초점이 가고 보내지 않는다', () => {
    renderWith(<PasswordCard />)
    fireEvent.change(screen.getByLabelText('현재 비밀번호'), { target: { value: 'dummy-current-1' } })
    fireEvent.click(screen.getByRole('button', { name: '비밀번호 바꾸기' }))

    expect(screen.getByRole('alert')).toBeTruthy()
    // "새 비밀번호" 칸의 이름에는 도움말이 같이 들어 있어서(다음 PR에서 분리 예정) 앞부분으로 찾는다
    expect(document.activeElement).toBe(screen.getAllByLabelText(/^새 비밀번호/)[0])
    expect(changePassword).not.toHaveBeenCalled()
  })

  it('회원 탈퇴: 비밀번호가 비면 비밀번호 칸, 체크가 없으면 체크칸으로 초점이 간다', () => {
    renderWith(<DeleteAccountCard />)
    fireEvent.click(screen.getByRole('button', { name: '회원 탈퇴 진행' }))
    const submit = screen.getByRole('button', { name: '영구히 탈퇴하기' })
    expect((submit as HTMLButtonElement).disabled).toBe(false)

    fireEvent.click(submit)
    expect(screen.getByRole('alert').textContent).toBe('현재 비밀번호를 입력해주세요.')
    expect(document.activeElement).toBe(screen.getByLabelText('현재 비밀번호'))

    fireEvent.change(screen.getByLabelText('현재 비밀번호'), { target: { value: 'dummy-current-1' } })
    fireEvent.click(submit)
    expect(screen.getByRole('alert').textContent).toBe('되돌릴 수 없다는 항목에 체크해주세요.')
    expect(document.activeElement).toBe(screen.getByRole('checkbox'))
    expect(deleteAccount).not.toHaveBeenCalled()
  })

  it('언어·시간대 저장 버튼은 바뀐 것이 없을 때 aria-disabled이고(초점은 닿는다), 눌러도 저장하지 않는다', async () => {
    fn(api.getPreferences).mockResolvedValue({
      preferred_locale: 'ko-KR',
      preferred_timezone: null,
      effective_timezone: 'Asia/Seoul',
    })
    renderWith(<SettingsPage />)
    const save = await screen.findByRole('button', { name: locale.t('common.save') })
    await waitFor(() => expect(save.getAttribute('aria-disabled')).toBe('true'))
    expect((save as HTMLButtonElement).disabled).toBe(false)
    save.focus()
    expect(document.activeElement).toBe(save)
    fireEvent.click(save)
    expect(fn(api.updatePreferences)).not.toHaveBeenCalled()
  })
})

describe('관심종목·주문 입력', () => {
  it('관심종목: 빈 칸으로 추가하면 안내와 함께 종목 입력칸으로 초점이 간다', async () => {
    listWatchlist.mockResolvedValue([])
    renderWith(<WatchlistSection />)
    const submit = await screen.findByRole('button', { name: '추가' })
    expect((submit as HTMLButtonElement).disabled).toBe(false)

    fireEvent.click(submit)
    expect(screen.getByRole('alert').textContent).toBe('종목명 또는 종목코드를 입력해주세요.')
    expect(document.activeElement).toBe(screen.getByLabelText('관심종목에 추가할 종목'))
    expect(addWatchlistItem).not.toHaveBeenCalled()
  })

  it('주문: 종목이 비면 안내와 함께 종목 입력칸으로 초점이 간다', () => {
    const portfolio = { cash_balance: 10_000_000, total_eval_amount: 0, total_asset: 10_000_000, holdings: [], note: null }
    renderWith(<HoldingsSection portfolio={portfolio as never} />)
    fireEvent.click(screen.getByRole('button', { name: '주문' }))

    expect(screen.getByRole('alert').textContent).toBe('주문할 종목을 입력해주세요.')
    expect(document.activeElement).toBe(screen.getByLabelText('주문할 종목'))
    expect(placeOrder).not.toHaveBeenCalled()
  })
})

describe('가로 스크롤 표', () => {
  it('ScrollRegion은 이름이 있는 영역이고 키보드 초점을 받는다', () => {
    render(
      <ScrollRegion className="table-scroll" label="보유 종목별 비중">
        <table />
      </ScrollRegion>,
    )
    const region = screen.getByRole('region', { name: '보유 종목별 비중' })
    expect(region.getAttribute('tabindex')).toBe('0')
    region.focus()
    expect(document.activeElement).toBe(region)
  })

  it('ETF 구성종목 표가 이름 있는 스크롤 영역 안에 있다', async () => {
    getEtfHoldings.mockResolvedValue({
      ticker: '069500',
      as_of_date: '2026-11-13',
      holding_count: 1,
      total_weight_pct: 10,
      top_weight_pct: 10,
      source: '운용사',
      stale: false,
      stale_notice: null,
      message: null,
      data_warnings: [],
      items: [{ rank: 1, name: '삼성전자', code: '005930', weight_pct: 10 }],
    })
    renderWith(<EtfHoldingsCard ticker="069500" />)
    // 카드(section)도 같은 이름의 영역이라, 표를 감싼 스크롤 영역(초점을 받는 것)을 찾는다
    const table = await screen.findByRole('table')
    const region = table.closest('[role="region"]')
    expect(region?.getAttribute('aria-label')).toBe('구성종목·편입 비중')
    expect(region?.getAttribute('tabindex')).toBe('0')
  })
})
