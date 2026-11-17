import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { SELECTABLE_LOCALES } from './api/contracts/common'
import { translate } from './i18n'
import { LocaleContext } from './i18n/localeContext'
import SettingsPage from './pages/SettingsPage'

// 일본어·중국어 번역은 더 이상 관리하지 않아서 설정의 언어 선택지에서 숨긴다(이미 그 언어로 저장한 계정의 현재 값은 보인다).
vi.mock('./api', () => ({
  USE_MOCK: false,
  api: { getPreferences: vi.fn(), updatePreferences: vi.fn(), me: vi.fn() },
}))
vi.mock('./auth/useAuth', () => ({ useAuth: () => ({ endSession: vi.fn() }) }))
vi.mock('./pages/settings/DeleteAccountCard', () => ({ default: () => null }))
vi.mock('./pages/settings/EmailCard', () => ({ default: () => null }))
vi.mock('./pages/settings/PasswordCard', () => ({ default: () => null }))
const { api } = await import('./api')
const getPreferences = api.getPreferences as unknown as ReturnType<typeof vi.fn>

const locale = {
  locale: 'ko-KR' as const,
  timezone: 'Asia/Seoul',
  preferences: undefined,
  t: (key: string, options?: { fallback?: string; vars?: Record<string, string | number> }) => translate('ko-KR', key, options),
}

function renderSettings() {
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <LocaleContext.Provider value={locale}>
        <MemoryRouter>
          <SettingsPage />
        </MemoryRouter>
      </LocaleContext.Provider>
    </QueryClientProvider>,
  )
}

const optionNames = (select: HTMLElement) => Array.from(select.querySelectorAll('option')).map((o) => o.textContent)

beforeEach(() => vi.clearAllMocks())

describe('설정의 언어 선택지', () => {
  it('선택할 수 있는 언어는 한국어와 영어뿐이다', () => {
    expect([...SELECTABLE_LOCALES]).toEqual(['ko-KR', 'en-US'])
  })

  it('일본어·중국어는 선택지에 보이지 않는다', async () => {
    getPreferences.mockResolvedValue({ preferred_locale: 'ko-KR', preferred_timezone: null, effective_timezone: 'Asia/Seoul' })
    renderSettings()
    const select = await screen.findByRole('combobox', { name: locale.t('settings.language') })
    expect(optionNames(select)).toEqual(['한국어', 'English'])
  })

  it('이미 일본어로 저장한 계정은 현재 값이 선택지에 남아 있어서 값이 사라지지 않는다', async () => {
    getPreferences.mockResolvedValue({ preferred_locale: 'ja-JP', preferred_timezone: null, effective_timezone: 'Asia/Seoul' })
    renderSettings()
    const select = (await screen.findByRole('combobox', { name: locale.t('settings.language') })) as HTMLSelectElement
    await vi.waitFor(() => expect(select.value).toBe('ja-JP'))
    expect(optionNames(select)).toEqual(['한국어', 'English', '日本語'])
  })
})
