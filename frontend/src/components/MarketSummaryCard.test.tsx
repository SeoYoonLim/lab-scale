import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import type { MarketOverview } from '../api/contracts/market'
import marketFixture from '../api/contracts/fixtures/market-overview.json'
import { translate } from '../i18n'
import { LocaleContext } from '../i18n/localeContext'
import MarketSummaryCard from './MarketSummaryCard'

vi.mock('../api', () => ({ api: { getMarketOverview: vi.fn() } }))
const { api } = await import('../api')
const getMarketOverview = api.getMarketOverview as unknown as ReturnType<typeof vi.fn>

function renderCard(data: MarketOverview) {
  getMarketOverview.mockResolvedValue(data)
  const locale = {
    locale: 'ko-KR' as const,
    timezone: 'Asia/Seoul',
    preferences: undefined,
    t: (key: string, options?: { fallback?: string; vars?: Record<string, string | number> }) => translate('ko-KR', key, options),
  }
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <LocaleContext.Provider value={locale}>
        <MemoryRouter>
          <MarketSummaryCard />
        </MemoryRouter>
      </LocaleContext.Provider>
    </QueryClientProvider>,
  )
}

// 문서(backend/API.md)의 실제 응답 예시를 그대로 쓴다. 타입 단언은 JSON의 문자열 리터럴이 넓게 추론되기 때문이다.
const base = marketFixture as unknown as MarketOverview

describe('MarketSummaryCard (market/overview)', () => {
  it('지수 종가·등락, 지연 띠, 수집 대상 기준 문구를 보여준다', async () => {
    renderCard(base)
    expect(await screen.findByText('코스피')).toBeTruthy()
    expect(screen.getByText('5,637.63')).toBeTruthy()
    expect(screen.getByText('-3.78%')).toBeTruthy()
    // delayed(5거래일): 지연 띠와 기준일
    expect(screen.getByRole('status').textContent).toContain('5거래일')
    // 상승·하락 수는 시장 전체가 아니라 수집 대상 기준이라고 같이 적는다
    expect(screen.getByText(/수집 대상 300종목 기준/)).toBeTruthy()
  })

  it('등락이 null이면 0%가 아니라 "—"와 이유를 보여준다', async () => {
    renderCard({
      ...base,
      indices: [
        { ...base.indices[0], change: null, change_pct: null, change_unavailable_reason: 'previous_trading_day_missing' },
        base.indices[1],
      ],
    })
    expect(await screen.findByText('—')).toBeTruthy()
    expect(screen.queryByText('0.00%')).toBeNull()
    expect(screen.getByText(/직전 거래일 종가가 수집되지 않아/)).toBeTruthy()
  })

  it('데이터가 없으면(no_data) 오류가 아니라 안내 문장이고 숫자는 그리지 않는다', async () => {
    renderCard({
      ...base,
      status: 'no_data',
      as_of: null,
      freshness: { ...base.freshness, status: 'no_data', lag_business_days: null },
      indices: base.indices.map((i) => ({ ...i, date: null, close: null, change: null, change_pct: null })),
      breadth: null,
    })
    expect(await screen.findByText('시장 데이터가 아직 없어요.')).toBeTruthy()
    expect(screen.queryByText('코스피')).toBeNull()
  })
})
