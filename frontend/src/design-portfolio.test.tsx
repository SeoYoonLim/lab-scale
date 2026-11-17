import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen } from '@testing-library/react'
import type { ReactElement } from 'react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import DiagnosisCompareView from './components/DiagnosisCompareView'
import DiagnosisResultView from './components/DiagnosisResultView'
import { translate } from './i18n'
import { LocaleContext } from './i18n/localeContext'
import PortfolioPage from './pages/PortfolioPage'
import HoldingsSection from './pages/portfolio/HoldingsSection'

// 진단 결과·포트폴리오의 색 디자인이 붙는 표식(클래스·속성)과, 장식(아이콘·색)이 이름과 글자를 바꾸지 않는지 확인한다.
vi.mock('./api', () => ({
  USE_MOCK: false,
  api: { getRealtimePrice: vi.fn(), placeOrder: vi.fn(), resetPortfolio: vi.fn() },
}))
vi.mock('./components/DiagnosisLookThrough', () => ({ default: () => null }))
const { api } = await import('./api')
const getRealtimePrice = api.getRealtimePrice as unknown as ReturnType<typeof vi.fn>

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
  getRealtimePrice.mockRejectedValue(new Error('없음'))
})

const result = {
  source: 'llm',
  model: 'llama3.1:8b',
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
  holdings: [],
  look_through: null,
  flags: [{ message: '쏠림' }],
  strengths: ['강점 한 줄'],
  risks: ['위험 한 줄'],
  suggestions: ['제안 한 줄'],
  notes: [],
  disclaimer: '면책',
}

describe('진단 결과의 색 칸', () => {
  it('강점=초록, 위험 요인·확인된 항목=주황, 제안=파랑 칸 표식이 붙는다', () => {
    const { container } = renderWith(<DiagnosisResultView result={result as never} />)
    expect(container.querySelectorAll('.diagnosis-col-strengths')).toHaveLength(1)
    // 위험 요인과 확인된 항목이 주황 톤이다
    expect(container.querySelectorAll('.diagnosis-col-risks')).toHaveLength(2)
    expect(container.querySelectorAll('.diagnosis-col-suggestions')).toHaveLength(1)
  })

  it('색 칸의 제목은 여전히 제목(h3)이고, 아이콘은 이름에 글자를 더하지 않는다', () => {
    const { container } = renderWith(<DiagnosisResultView result={result as never} />)
    const titles = Array.from(container.querySelectorAll('h3.diagnosis-col-title'))
    // 화면과 같은 방식(번역 자원이 있으면 그것, 없으면 코드 안 기본 문구)으로 기대 문구를 만든다
    expect(titles.map((h) => h.textContent)).toEqual([
      locale.t('diagnosis.flags_title', { fallback: '확인된 항목' }),
      locale.t('diagnosis.strengths', { fallback: '강점' }),
      locale.t('diagnosis.risks', { fallback: '위험 요인' }),
      locale.t('diagnosis.suggestions', { fallback: '제안' }),
    ])
    for (const title of titles) {
      const icon = title.querySelector('svg')
      expect(icon?.getAttribute('aria-hidden')).toBe('true')
    }
  })

  it('출처가 AI 설명이면 배지로 보이고 모델 이름이 함께 나온다', () => {
    const { container } = renderWith(<DiagnosisResultView result={result as never} />)
    expect(container.querySelector('.status-badge-accent')?.textContent).toContain('llama3.1:8b')
  })

  it('비교 화면의 새 항목은 주황, 해소된 항목은 초록 칸이다', () => {
    const compare = {
      previous: { created_at: '2026-11-10T09:00:00+09:00' },
      current: { created_at: '2026-11-13T09:00:00+09:00' },
      basis: null,
      metric_changes: null,
      exposure_changes: null,
      changes: {
        total_asset: { previous: 1000, current: 1100, change_amount: 100, change_pct: 10 },
        holdings: [],
        flags: { added: [{ message: '새 항목' }], resolved: [{ message: '해소' }] },
        returns_20d: [],
      },
      disclaimer: '면책',
    }
    const { container } = renderWith(<DiagnosisCompareView compare={compare as never} />)
    expect(container.querySelectorAll('.diagnosis-col-risks')).toHaveLength(1)
    expect(container.querySelectorAll('.diagnosis-col-strengths')).toHaveLength(1)
  })
})

describe('포트폴리오 탭', () => {
  it('탭 6개가 아이콘을 가져도 링크 이름은 글자 그대로이다', () => {
    renderWith(
      <Routes>
        <Route path="/portfolio" element={<PortfolioPage />} />
      </Routes>,
      ['/portfolio'],
    )
    const names = ['보유·주문', '성과', '실질 노출', 'AI 진단', '체결 내역', '관심종목']
    for (const name of names) {
      const link = screen.getByRole('link', { name })
      expect(link.querySelector('.portfolio-tab-icon svg')?.getAttribute('aria-hidden')).toBe('true')
    }
    expect(screen.getByRole('link', { name: '보유·주문' }).getAttribute('aria-current')).toBe('page')
  })
})

describe('보유·주문의 색', () => {
  const holding = (ticker: string, name: string, pl: number | null, pct: number | null) => ({
    ticker,
    company_name: name,
    asset_type: 'stock',
    quantity: 1,
    avg_price: 1000,
    current_price: 1100,
    is_realtime: true,
    profit_loss: pl,
    profit_loss_pct: pct,
  })
  const portfolio = {
    cash_balance: 1_000_000,
    total_eval_amount: 300_000,
    total_asset: 1_300_000,
    note: null,
    holdings: [holding('000001', '오른종목', 100, 10), holding('000002', '내린종목', -100, -10), holding('000003', '그대로종목', 0, 0)],
  }

  it('요약 카드 3개(현금·평가금액·총 자산)가 따로 구분된다', () => {
    const { container } = renderWith(<HoldingsSection portfolio={portfolio as never} />)
    expect(container.querySelector('.portfolio-stat-cash')?.textContent).toContain('현금')
    expect(container.querySelector('.portfolio-stat-eval')?.textContent).toContain('평가금액')
    expect(container.querySelector('.portfolio-stat-total')?.textContent).toContain('총 자산')
  })

  it('보유 종목 행에 손익 방향 표식이 붙고, 손익 글자와 부호는 그대로 있다', () => {
    const { container } = renderWith(<HoldingsSection portfolio={portfolio as never} />)
    const rows = Array.from(container.querySelectorAll('.holding-row'))
    expect(rows.map((r) => ['up', 'down', 'flat'].find((k) => r.classList.contains(`holding-${k}`)))).toEqual(['up', 'down', 'flat'])
    const pills = Array.from(container.querySelectorAll('.pl-pill'))
    expect(pills.map((p) => p.classList.contains('up'))).toEqual([true, false, false])
    // 색에만 의존하지 않는다: 손실은 글자에 마이너스 부호가 있다
    expect(pills[1].textContent).toContain('-')
  })

  it('매수/매도 선택의 색 표식이 고른 값에 따라 바뀐다', () => {
    renderWith(<HoldingsSection portfolio={portfolio as never} />)
    const side = screen.getByLabelText('매수/매도')
    expect(side.getAttribute('data-side')).toBe('buy')
    fireEvent.change(side, { target: { value: 'sell' } })
    expect(side.getAttribute('data-side')).toBe('sell')
  })
})
