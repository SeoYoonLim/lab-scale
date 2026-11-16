import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { ReactElement } from 'react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import BriefingView from './components/briefing/BriefingView'
import DiagnosisCompareView from './components/DiagnosisCompareView'
import DiagnosisResultView from './components/DiagnosisResultView'
import { translate } from './i18n'
import { LocaleContext } from './i18n/localeContext'
import CompanyListPage from './pages/CompanyListPage'
import SettingsPage from './pages/SettingsPage'
import EmailCard from './pages/settings/EmailCard'
import PasswordCard from './pages/settings/PasswordCard'

// 제목 구조(진단 결과·비교), 필터 선택 상태와 결과 개수 알림(종목 목록), 입력칸 이름과 도움말 분리(설정),
// 새 탭 링크 안내(브리핑)를 확인한다.
vi.mock('./api', () => ({
  USE_MOCK: false,
  api: { searchCompanies: vi.fn(), getPreferences: vi.fn(), updatePreferences: vi.fn(), me: vi.fn() },
}))
vi.mock('./auth/useAuth', () => ({ useAuth: () => ({ endSession: vi.fn() }) }))
// 이 테스트와 무관한 조각은 비운다
vi.mock('./components/DiagnosisLookThrough', () => ({ default: () => null }))
vi.mock('./pages/settings/DeleteAccountCard', () => ({ default: () => null }))
const { api } = await import('./api')
const fn = (f: unknown) => f as ReturnType<typeof vi.fn>
const searchCompanies = fn(api.searchCompanies)

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
  fn(api.me).mockResolvedValue({ email: null, email_verified: false })
})

describe('진단 결과·비교의 제목 구조', () => {
  const result = {
    source: 'rule_based',
    model: null,
    generated_at: '2026-11-13T09:00:00+09:00',
    summary: '요약',
    metrics: {
      total_asset: 1000,
      cash_weight_pct: 10,
      top1_weight_pct: 50,
      top3_weight_pct: 90,
      herfindahl_index: 0.4,
      total_profit_loss: 0,
      total_profit_loss_pct: 0,
      market_weights_pct: {},
    },
    holdings: [
      { ticker: '005930', company_name: '삼성전자', asset_type: 'stock', market: 'KOSPI', weight_pct: 50, return_20d_pct: 1, profit_loss: 0, profit_loss_pct: 0, price_unavailable: false },
    ],
    look_through: null,
    flags: [{ message: '쏠림' }],
    strengths: ['강점 한 줄'],
    risks: [],
    suggestions: [],
    notes: [],
    disclaimer: '면책',
  }

  it('작은 소제목들이 일반 글자가 아니라 제목(h3)이다', () => {
    renderWith(<DiagnosisResultView result={result as never} />)
    const names = screen.getAllByRole('heading', { level: 3 }).map((h) => h.textContent)
    // 화면과 같은 방식(번역 자원이 있으면 그것, 없으면 코드 안 기본 문구)으로 기대 문구를 만든다
    const expected = [
      locale.t('diagnosis.holdings_title', { fallback: '보유 종목별 비중' }),
      locale.t('diagnosis.flags_title', { fallback: '확인된 항목' }),
      locale.t('diagnosis.strengths', { fallback: '강점' }),
      locale.t('diagnosis.risks', { fallback: '위험 요인' }),
      locale.t('diagnosis.suggestions', { fallback: '제안' }),
    ]
    for (const title of expected) expect(names).toContain(title)
  })

  it('비교 화면의 소제목도 제목(h3)이다', () => {
    const compare = {
      previous: { created_at: '2026-11-10T09:00:00+09:00' },
      current: { created_at: '2026-11-13T09:00:00+09:00' },
      basis: null,
      metric_changes: null,
      exposure_changes: null,
      changes: {
        total_asset: { previous: 1000, current: 1100, change_amount: 100, change_pct: 10 },
        holdings: [],
        flags: { added: [{ message: '새 항목' }], resolved: [] },
        returns_20d: [],
      },
      disclaimer: '면책',
    }
    renderWith(<DiagnosisCompareView compare={compare as never} />)
    const names = screen.getAllByRole('heading', { level: 3 }).map((h) => h.textContent)
    expect(names).toContain(locale.t('diagnosis.change_total_asset'))
    expect(names).toContain(locale.t('diagnosis.flags_added'))
    expect(names).toContain(locale.t('diagnosis.flags_resolved'))
  })
})

describe('종목 목록', () => {
  const companies = [
    { ticker: '005930', name: '삼성전자', market: 'KOSPI', asset_type: 'stock', latest_close: 70000, change_pct: 1 },
    { ticker: '000660', name: 'SK하이닉스', market: 'KOSPI', asset_type: 'stock', latest_close: 200000, change_pct: -1 },
  ]

  it('유형 필터가 선택 상태(aria-pressed)를 알린다', async () => {
    searchCompanies.mockResolvedValue(companies)
    renderWith(<CompanyListPage />)
    const all = await screen.findByRole('button', { name: '전체' })
    expect(all.getAttribute('aria-pressed')).toBe('true')
    expect(screen.getByRole('button', { name: 'ETF' }).getAttribute('aria-pressed')).toBe('false')

    fireEvent.click(screen.getByRole('button', { name: 'ETF' }))
    expect(screen.getByRole('button', { name: 'ETF' }).getAttribute('aria-pressed')).toBe('true')
    expect(screen.getByRole('button', { name: '전체' }).getAttribute('aria-pressed')).toBe('false')
  })

  it('검색이 끝나면 결과 개수를 알림 영역에 알린다', async () => {
    searchCompanies.mockResolvedValue(companies)
    renderWith(<CompanyListPage />)
    await waitFor(() => expect(screen.getAllByRole('status').some((el) => el.textContent === '검색 결과 2개')).toBe(true))
  })

  it('결과가 없어도 개수(0개)를 알린다', async () => {
    searchCompanies.mockResolvedValue([])
    renderWith(<CompanyListPage />)
    await waitFor(() => expect(screen.getAllByRole('status').some((el) => el.textContent === '검색 결과 0개')).toBe(true))
  })
})

describe('설정 입력칸의 이름과 도움말', () => {
  it('언어·시간대 입력칸의 이름은 라벨 글자뿐이고, 도움말은 설명으로 연결된다', async () => {
    fn(api.getPreferences).mockResolvedValue({ preferred_locale: 'ko-KR', preferred_timezone: null, effective_timezone: 'Asia/Seoul' })
    renderWith(<SettingsPage />)
    const language = await screen.findByRole('combobox', { name: locale.t('settings.language') })
    const timezone = screen.getByRole('combobox', { name: locale.t('settings.timezone') })
    for (const [input, rule] of [
      [language, locale.t('settings.language_help')],
      [timezone, locale.t('settings.timezone_help')],
    ] as const) {
      const help = document.getElementById(input.getAttribute('aria-describedby') ?? '')
      expect(help?.textContent).toContain(rule)
    }
  })

  it('이메일 카드의 현재 비밀번호 칸 이름에 도움말이 섞이지 않는다', async () => {
    renderWith(<EmailCard />)
    const input = await screen.findByLabelText('현재 비밀번호')
    const help = document.getElementById(input.getAttribute('aria-describedby') ?? '')
    expect(help?.textContent).toContain('본인 확인')
  })

  it('비밀번호 변경 카드의 새 비밀번호 칸 이름은 "새 비밀번호"이고 규칙은 설명으로 연결된다', () => {
    renderWith(<PasswordCard />)
    const input = screen.getByLabelText('새 비밀번호', { exact: true })
    const help = document.getElementById(input.getAttribute('aria-describedby') ?? '')
    expect(help?.textContent).toContain('8')
  })
})

describe('브리핑 링크', () => {
  it('새 탭으로 열리는 확인할 사항 링크에 안내가 붙는다', () => {
    const content = {
      title: '오늘의 브리핑',
      section_order: ['checklist'],
      market_data_status: 'latest',
      freshness: { status: 'latest', warning: false, headline: '최신이에요', notices: [] },
      summary: { source: 'code', text: '요약', note: null },
      sections: {
        checklist: { title: '확인할 사항', status: 'ok', message: null, items: [{ kind: 'news', text: '공시 확인', url: 'https://example.com/a' }] },
      },
      not_advice: '투자 권유가 아니에요',
      disclaimer: '면책',
    }
    renderWith(<BriefingView content={content as never} />)
    const link = screen.getByRole('link', { name: /공시 확인/ })
    expect(link.textContent).toContain(locale.t('a11y.opens_new_tab', { fallback: '새 탭에서 열림' }))
  })
})
