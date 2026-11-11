import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { HistoryItem, HistorySearchResponse } from '../api/contracts/history'
import { translate } from '../i18n'
import { LocaleContext } from '../i18n/localeContext'
import HistoryPage from './HistoryPage'

vi.mock('../api', () => ({
  api: { searchHistory: vi.fn(), deleteReport: vi.fn(), deleteDiagnosis: vi.fn() },
}))
const { api } = await import('../api')
const searchHistory = api.searchHistory as unknown as ReturnType<typeof vi.fn>
const deleteReport = api.deleteReport as unknown as ReturnType<typeof vi.fn>
const deleteDiagnosis = api.deleteDiagnosis as unknown as ReturnType<typeof vi.fn>

const research: HistoryItem = {
  kind: 'research',
  id: 11,
  created_at: '2026-11-11T08:44:00Z',
  local_date: '2026-11-11',
  local_time: '17:44',
  date_group: 'today',
  title: '삼성전자 오늘 왜 올랐어?',
  summary: '요약 문장',
  locale: 'ko-KR',
  company_names: ['삼성전자'],
  tickers: ['005930'],
  detail_path: '/research/11',
  research: { report_id: 11, previous_report_id: null, question: '삼성전자 오늘 왜 올랐어?', analysis_type: 'general', analysis_types: ['general'], source_count: 0, used_tools: [] },
  diagnosis: null,
}
const diagnosis: HistoryItem = {
  kind: 'diagnosis',
  id: 21,
  created_at: '2026-11-11T08:45:00Z',
  local_date: '2026-11-11',
  local_time: '17:45',
  date_group: 'today',
  title: '삼성전자 외 1종목 진단',
  summary: null,
  locale: 'ko-KR',
  company_names: [],
  tickers: [],
  detail_path: '/diagnosis/21',
  research: null,
  diagnosis: { diagnosis_id: 21, source: 'rule_based', total_asset: 10_000_000, holdings_count: 2, cash_weight_pct: 72.27, top_holdings: [], risk_summary: null, flags: [], flag_codes: [], generated_at: null },
}

function response(items: HistoryItem[], type: HistorySearchResponse['type'] = 'all'): HistorySearchResponse {
  return {
    type,
    keyword: null,
    terms: [],
    date_from: null,
    date_to: null,
    timezone: 'Asia/Seoul',
    sort: 'created_at,desc',
    page: 1,
    size: 10,
    total: items.length,
    total_pages: 1,
    has_next: false,
    counts: { all: 2, research: 1, diagnosis: 1 },
    items,
  }
}

function renderPage() {
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
          <HistoryPage />
        </MemoryRouter>
      </LocaleContext.Provider>
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  searchHistory.mockImplementation((params: { type?: string }) =>
    Promise.resolve(response(params.type === 'research' ? [research] : params.type === 'diagnosis' ? [diagnosis] : [diagnosis, research], (params.type as 'all') ?? 'all')),
  )
  deleteReport.mockResolvedValue(undefined)
  deleteDiagnosis.mockResolvedValue(undefined)
})

describe('내 기록: 유형 탭', () => {
  it('탭 3개와 개수 배지를 보여주고, 활성 탭만 aria-selected이며 Tab 키 순서에 든다', async () => {
    renderPage()
    // 탭은 먼저 그려지고 개수 배지는 응답이 오면 붙는다
    await waitFor(() => expect(document.querySelectorAll('.history-tab-count').length).toBe(3))
    const tabs = screen.getAllByRole('tab')
    expect(tabs.map((tab) => tab.textContent)).toEqual(['전체2', 'AI 리서치1', '포트폴리오 진단1'])
    expect(tabs.map((tab) => tab.getAttribute('aria-selected'))).toEqual(['true', 'false', 'false'])
    expect(tabs.map((tab) => tab.tabIndex)).toEqual([0, -1, -1])
    // 개수는 글자에 붙지 않고 따로 스타일되는 작은 배지다
    expect(tabs[0].querySelector('.history-tab-count')?.textContent).toBe('2')
    expect(screen.getByRole('tablist').getAttribute('aria-label')).toBe('기록 유형')
  })

  it('탭을 누르면 그 type으로 다시 요청하고 첫 페이지부터 본다', async () => {
    renderPage()
    fireEvent.click(await screen.findByRole('tab', { name: /포트폴리오 진단/ }))
    await waitFor(() => expect(searchHistory).toHaveBeenLastCalledWith(expect.objectContaining({ type: 'diagnosis', page: 1 })))
    expect(screen.getByRole('tab', { name: /포트폴리오 진단/ }).getAttribute('aria-selected')).toBe('true')
  })

  it('방향키·Home·End로 탭을 옮기고 포커스가 따라간다', async () => {
    renderPage()
    const first = await screen.findByRole('tab', { name: /^전체/ })
    first.focus()
    fireEvent.keyDown(first, { key: 'ArrowRight' })
    await waitFor(() => expect(searchHistory).toHaveBeenLastCalledWith(expect.objectContaining({ type: 'research' })))
    expect(document.activeElement?.id).toBe('history-tab-research')
    fireEvent.keyDown(document.activeElement!, { key: 'End' })
    await waitFor(() => expect(searchHistory).toHaveBeenLastCalledWith(expect.objectContaining({ type: 'diagnosis' })))
    fireEvent.keyDown(document.activeElement!, { key: 'ArrowRight' }) // 마지막에서 처음으로
    await waitFor(() => expect(searchHistory).toHaveBeenLastCalledWith(expect.objectContaining({ type: 'all' })))
  })
})

describe('내 기록: 검색·날짜·정렬', () => {
  it('검색어·정렬이 요청에 들어가고, 시작일이 종료일보다 늦으면 보내지 않고 안내한다', async () => {
    renderPage()
    await screen.findAllByRole('tab')
    fireEvent.change(screen.getByRole('searchbox'), { target: { value: '삼성' } })
    fireEvent.click(screen.getByRole('button', { name: '검색' }))
    await waitFor(() => expect(searchHistory).toHaveBeenLastCalledWith(expect.objectContaining({ keyword: '삼성' })))

    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'created_at,asc' } })
    await waitFor(() => expect(searchHistory).toHaveBeenLastCalledWith(expect.objectContaining({ sort: 'created_at,asc' })))

    const [from, to] = screen.getAllByDisplayValue('').filter((el) => (el as HTMLInputElement).type === 'date') as HTMLInputElement[]
    fireEvent.change(to, { target: { value: '2026-11-01' } })
    await waitFor(() => expect(searchHistory).toHaveBeenLastCalledWith(expect.objectContaining({ date_to: '2026-11-01' })))
    const calls = searchHistory.mock.calls.length
    fireEvent.change(from, { target: { value: '2026-11-05' } })
    expect((await screen.findByRole('alert')).textContent).toContain('시작일이 종료일보다 늦')
    expect(from.getAttribute('aria-invalid')).toBe('true')
    expect(searchHistory.mock.calls.length).toBe(calls)
  })
})

describe('내 기록: 삭제 확인', () => {
  it('삭제를 누르면 브라우저 confirm 대신 카드 안에 확인 영역이 열리고, 기본 포커스는 취소다', async () => {
    const nativeConfirm = vi.spyOn(window, 'confirm')
    renderPage()
    const card = (await screen.findByText('삼성전자 오늘 왜 올랐어?')).closest('article') as HTMLElement
    fireEvent.click(within(card).getByRole('button', { name: '삭제' }))
    const dialog = within(card).getByRole('alertdialog')
    expect(dialog.textContent).toContain('이 리포트를 삭제할까요? 되돌릴 수 없어요.')
    expect(document.activeElement?.textContent).toBe('취소')
    expect(nativeConfirm).not.toHaveBeenCalled()
    expect(deleteReport).not.toHaveBeenCalled()
  })

  it('Esc와 취소는 삭제 없이 닫는다', async () => {
    renderPage()
    const card = (await screen.findByText('삼성전자 오늘 왜 올랐어?')).closest('article') as HTMLElement
    fireEvent.click(within(card).getByRole('button', { name: '삭제' }))
    fireEvent.keyDown(within(card).getByRole('alertdialog'), { key: 'Escape' })
    expect(within(card).queryByRole('alertdialog')).toBeNull()
    fireEvent.click(within(card).getByRole('button', { name: '삭제' }))
    fireEvent.click(within(card).getByRole('button', { name: '취소' }))
    expect(within(card).queryByRole('alertdialog')).toBeNull()
    expect(deleteReport).not.toHaveBeenCalled()
    expect(deleteDiagnosis).not.toHaveBeenCalled()
  })

  it('확정하면 종류에 맞는 API로 지운다(리서치는 deleteReport, 진단은 deleteDiagnosis)', async () => {
    renderPage()
    const researchCard = (await screen.findByText('삼성전자 오늘 왜 올랐어?')).closest('article') as HTMLElement
    fireEvent.click(within(researchCard).getByRole('button', { name: '삭제' }))
    await act(async () => {
      fireEvent.click(within(within(researchCard).getByRole('alertdialog')).getByRole('button', { name: '삭제' }))
    })
    await waitFor(() => expect(deleteReport).toHaveBeenCalledWith(11))

    const diagnosisCard = screen.getByText('삼성전자 외 1종목 진단').closest('article') as HTMLElement
    fireEvent.click(within(diagnosisCard).getByRole('button', { name: '삭제' }))
    await act(async () => {
      fireEvent.click(within(within(diagnosisCard).getByRole('alertdialog')).getByRole('button', { name: '삭제' }))
    })
    await waitFor(() => expect(deleteDiagnosis).toHaveBeenCalledWith(21))
  })
})
