import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { ReactElement } from 'react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import disclosuresFixture from '../api/contracts/fixtures/stock-disclosures.json'
import newsFixture from '../api/contracts/fixtures/stock-news.json'
import type { StockDisclosures, StockNews } from '../api/contracts/stockFeed'
import { translate } from '../i18n'
import { LocaleContext } from '../i18n/localeContext'
import type { PriceHistoryResponse } from '../types'
import PriceChartCard from './PriceChartCard'
import StockDisclosuresCard from './StockDisclosuresCard'
import StockNewsCard from './StockNewsCard'

vi.mock('../api', () => ({
  api: { getStockNews: vi.fn(), getStockDisclosures: vi.fn(), getPriceHistory: vi.fn() },
}))
const { api } = await import('../api')
const getStockNews = api.getStockNews as unknown as ReturnType<typeof vi.fn>
const getStockDisclosures = api.getStockDisclosures as unknown as ReturnType<typeof vi.fn>
const getPriceHistory = api.getPriceHistory as unknown as ReturnType<typeof vi.fn>

function renderWithProviders(ui: ReactElement) {
  const locale = {
    locale: 'ko-KR' as const,
    timezone: 'Asia/Seoul',
    preferences: undefined,
    t: (key: string, options?: { fallback?: string; vars?: Record<string, string | number> }) => translate('ko-KR', key, options),
  }
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <LocaleContext.Provider value={locale}>
        <MemoryRouter>{ui}</MemoryRouter>
      </LocaleContext.Provider>
    </QueryClientProvider>,
  )
}

// 문서(backend/API.md)의 실제 응답 예시. 타입 단언은 JSON의 문자열 리터럴이 넓게 추론되기 때문이다.
const news = newsFixture as unknown as StockNews
const disclosures = disclosuresFixture as unknown as StockDisclosures

beforeEach(() => {
  vi.clearAllMocks()
})

describe('StockNewsCard', () => {
  it('제목 링크·출처·KST 날짜·함께 언급된 종목을 보여주고, 수집이 멈췄으면 지연 띠를 띄운다', async () => {
    getStockNews.mockResolvedValue(news)
    renderWithProviders(<StockNewsCard ticker="005930" />)
    const title = await screen.findByRole('link', { name: news.items[0].title })
    expect(title.getAttribute('href')).toBe(news.items[0].url)
    expect(title.getAttribute('rel')).toContain('noopener')
    expect(screen.getByText('www.thereport.co.kr')).toBeTruthy()
    // 게시 시각은 KST 오프셋을 읽어 한국 날짜로 보여준다(2026-11-03T19:48:00+09:00)
    expect(screen.getByText(/2026\.11\.03/)).toBeTruthy()
    // 같은 기사가 연결된 다른 종목은 종목 상세 링크다
    expect(screen.getByRole('link', { name: '000270' }).getAttribute('href')).toBe('/companies/000270')
    // 전체 수집이 5평일 멈춘 상태(stale)
    expect(screen.getByRole('status').textContent).toContain('5평일째')
    expect(screen.getByText(/해석하지 않은 원문/)).toBeTruthy()
  })

  it('비었으면 오류가 아니라 안내이고, 마지막 수집이 실패했으면 그 사실을 알린다', async () => {
    getStockNews.mockResolvedValue({ ...news, total: 0, total_pages: 0, has_next: false, items: [], freshness: { ...news.freshness, stale: false } })
    const first = renderWithProviders(<StockNewsCard ticker="005930" />)
    expect(await screen.findByText('이 기간에 저장된 뉴스가 없어요.')).toBeTruthy()
    first.unmount()

    getStockNews.mockResolvedValue({
      ...news,
      total: 0,
      items: [],
      freshness: { ...news.freshness, stale: false, last_collection: { ...news.freshness.last_collection!, status: 'failed' } },
    })
    renderWithProviders(<StockNewsCard ticker="005930" />)
    expect(await screen.findByText('최근 수집에 실패해서 새 항목이 없을 수 있어요.')).toBeTruthy()
  })

  it('날짜 범위가 거꾸로면 서버(422)에 보내지 않고 안내만 보여준다', async () => {
    getStockNews.mockResolvedValue(news)
    renderWithProviders(<StockNewsCard ticker="005930" />)
    await screen.findByRole('link', { name: news.items[0].title })
    const calls = getStockNews.mock.calls.length
    const [from, to] = screen.getAllByDisplayValue('')
      .filter((el) => (el as HTMLInputElement).type === 'date') as HTMLInputElement[]
    fireEvent.change(to, { target: { value: '2026-11-01' } })
    await waitFor(() => expect(getStockNews.mock.calls.length).toBeGreaterThan(calls))
    const afterTo = getStockNews.mock.calls.length
    fireEvent.change(from, { target: { value: '2026-11-05' } })
    expect((await screen.findByRole('alert')).textContent).toContain('시작일이 종료일보다 늦어요')
    expect(getStockNews.mock.calls.length).toBe(afterTo)
  })

  it('필터를 바꾸면 첫 페이지부터 다시 불러온다(페이지 이동 뒤에도 offset을 0으로)', async () => {
    getStockNews.mockResolvedValue({ ...news, total: 25, total_pages: 3, has_next: true, limit: 10, offset: 0 })
    renderWithProviders(<StockNewsCard ticker="005930" />)
    await screen.findByRole('link', { name: news.items[0].title })
    fireEvent.click(screen.getByRole('button', { name: '다음' }))
    await waitFor(() => expect(getStockNews).toHaveBeenLastCalledWith('005930', expect.objectContaining({ offset: 10 })))
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'asc' } })
    await waitFor(() => expect(getStockNews).toHaveBeenLastCalledWith('005930', expect.objectContaining({ offset: 0, order: 'asc' })))
  })
})

describe('StockDisclosuresCard', () => {
  it('정정 공시에 배지를 달고, 접수일은 시각 없이 날짜만 보여준다', async () => {
    getStockDisclosures.mockResolvedValue({ ...disclosures, freshness: { ...disclosures.freshness, stale: false } })
    renderWithProviders(<StockDisclosuresCard ticker="005930" />)
    const link = await screen.findByRole('link', { name: disclosures.items[0].title })
    expect(link.getAttribute('href')).toBe(disclosures.items[0].source_url)
    expect(screen.getByText('정정')).toBeTruthy()
    expect(screen.getByText('2026.10.29')).toBeTruthy()
    // disclosed_at(T00:00:00+09:00)을 시각으로 보여주지 않는다
    expect(screen.queryByText(/00:00/)).toBeNull()
  })

  it('정정 필터를 바꾸면 correction 파라미터로 다시 부른다', async () => {
    getStockDisclosures.mockResolvedValue(disclosures)
    renderWithProviders(<StockDisclosuresCard ticker="005930" />)
    await screen.findByRole('link', { name: disclosures.items[0].title })
    fireEvent.change(screen.getByDisplayValue('전체'), { target: { value: 'only' } })
    await waitFor(() => expect(getStockDisclosures).toHaveBeenLastCalledWith('005930', expect.objectContaining({ correction: 'only', offset: 0 })))
  })

  it('ETF는 오류가 아니라 서버 안내(supported=false)를 보여주고 필터는 그리지 않는다', async () => {
    getStockDisclosures.mockResolvedValue({
      ...disclosures,
      asset_type: 'etf',
      supported: false,
      message: 'ETF는 DART 공시 수집 대상이 아니어서 공시 목록이 없습니다. 뉴스 목록(GET /api/stocks/069500/news)을 이용하세요.',
      total: 0,
      total_pages: 0,
      items: [],
    })
    renderWithProviders(<StockDisclosuresCard ticker="069500" />)
    expect(await screen.findByText(/DART 공시 수집 대상이 아니어서/)).toBeTruthy()
    // 서버 message에 든 개발자용 API 경로는 보여주지 않는다
    expect(screen.queryByText(/GET \/api/)).toBeNull()
    expect(screen.queryByText('시작일')).toBeNull()
  })
})

describe('PriceChartCard 기간 버튼(180·365)', () => {
  const history = (available: number | undefined, days = 60, missing = 0): PriceHistoryResponse => ({
    ticker: '005930',
    company_name: '삼성전자',
    asset_type: 'stock',
    days,
    count: 5,
    price_as_of: '2026-11-03',
    items: ['2026-10-28', '2026-10-29', '2026-10-30', '2026-11-02', '2026-11-03'].map((date, i) => ({
      date,
      close: 70000 + i * 100,
      volume: 1000,
      change_pct: 0.1,
      open: 70000,
      high: 70500,
      low: 69500,
    })),
    available_count: available,
    first_date: '2026-06-26',
    missing_trading_days: missing,
  })

  it('저장된 행이 모자라면 180·365 버튼을 끄고 이유를 알려준다', async () => {
    getPriceHistory.mockResolvedValue(history(73))
    renderWithProviders(<PriceChartCard ticker="005930" />)
    // 버튼은 데이터가 오기 전에 먼저 그려지고, 응답의 available_count가 오면 꺼진다
    const long = await screen.findByRole('button', { name: '365거래일' })
    await waitFor(() => expect((long as HTMLButtonElement).disabled).toBe(true))
    expect(long.getAttribute('title')).toBe('73거래일만 있어요')
    expect((screen.getByRole('button', { name: '180거래일' }) as HTMLButtonElement).disabled).toBe(true)
    expect(screen.getByText(/저장된 주가가 73거래일이라/)).toBeTruthy()
    // 90 이하는 모자라도 눌러서 "적게 보여드려요" 안내를 보게 한다
    expect((screen.getByRole('button', { name: '90거래일' }) as HTMLButtonElement).disabled).toBe(false)
  })

  it('충분하면 365거래일로 다시 부르고, 수집이 빠진 거래일과 수정주가 가능성을 알려준다', async () => {
    getPriceHistory.mockImplementation((_ticker: string, days: number) => Promise.resolve(history(400, days, 2)))
    renderWithProviders(<PriceChartCard ticker="005930" />)
    const long = await screen.findByRole('button', { name: '365거래일' })
    expect((long as HTMLButtonElement).disabled).toBe(false)
    fireEvent.click(long)
    await waitFor(() => expect(getPriceHistory).toHaveBeenLastCalledWith('005930', 365))
    expect(await screen.findByText(/수집되지 않은 거래일이 2일 있어요/)).toBeTruthy()
    expect(screen.getByText(/일부 구간은 수정주가일 수 있어요/)).toBeTruthy()
    expect(screen.queryByText(/분할·배당 보정은 하지 않은/)).toBeNull()
  })

  it('옛 서버 응답(available_count 없음)에서는 버튼을 끄지 않는다', async () => {
    getPriceHistory.mockResolvedValue(history(undefined))
    renderWithProviders(<PriceChartCard ticker="005930" />)
    const long = await screen.findByRole('button', { name: '365거래일' })
    expect((long as HTMLButtonElement).disabled).toBe(false)
  })
})
