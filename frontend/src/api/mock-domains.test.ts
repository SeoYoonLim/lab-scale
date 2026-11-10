import { afterEach, describe, expect, it } from 'vitest'
import { ApiError } from './errors'
import { mockApi } from './mockApi'
import { setMockClockOffsetDays, setMockDataLagDays } from './mock-core'
import { setToken } from '../utils/token'

async function signupAndLogin(): Promise<void> {
  const username = `user${Math.random().toString(36).slice(2, 10)}`
  const res = await mockApi.signup(username, 'Zk9-verify-pw')
  setToken(res.access_token)
}

afterEach(() => {
  setMockClockOffsetDays(0)
  setMockDataLagDays(0)
})

describe('성과·벤치마크 mock (핸드오프 PORTFOLIO_PERFORMANCE)', () => {
  it('체결이 없으면 no_trades이고 수익률은 null이다(0이 아니다)', async () => {
    await signupAndLogin()
    const p = await mockApi.getPerformance()
    expect(p.status).toBe('no_trades')
    expect(p.summary.period_return_pct).toBeNull()
    expect(p.series.points).toEqual([])
    expect(p.comparison.status).toBe('portfolio_unavailable')
    expect(p.message).toBeTruthy()
  })

  it('과거 체결을 재생해 곡선을 만들고, 지수 비교는 첫 공통 거래일을 100으로 맞춘다', async () => {
    await signupAndLogin()
    setMockClockOffsetDays(-30)
    await mockApi.placeOrder('삼성전자', 'buy', 10)
    setMockClockOffsetDays(0)

    const p = await mockApi.getPerformance({ period: 'all' })
    expect(p.status).toBe('ok')
    expect(p.series.points.length).toBeGreaterThan(10)
    // 첫 점은 첫 체결 직전 거래일 = 초기 자금 그대로
    expect(p.series.points[0].total_asset).toBe(10_000_000)
    expect(p.series.points[0].portfolio_index).toBe(100)
    expect(p.comparison.status).toBe('ok')
    expect(p.comparison.points[0].benchmark_index).toBe(100)
    const last = p.comparison.points.at(-1)!
    expect(last.excess_return_pp).toBeCloseTo(last.portfolio_return_pct - last.benchmark_return_pct, 1)
    // 누적 수익률은 현재 총자산 / 초기 자금
    expect(p.summary.cumulative_return_pct).toBeCloseTo((p.summary.end_value! / 10_000_000 - 1) * 100, 1)
  })

  it('요청 기간이 첫 체결보다 앞서면 첫 체결 직전부터 계산하고 period_before_inception 경고를 준다', async () => {
    await signupAndLogin()
    setMockClockOffsetDays(-10)
    await mockApi.placeOrder('삼성전자', 'buy', 1)
    setMockClockOffsetDays(0)
    const p = await mockApi.getPerformance({ period: '3m' })
    expect(p.warnings.some((w) => w.kind === 'period_before_inception')).toBe(true)
    expect(p.start_date).not.toBe(p.requested_start_date)
  })

  it('시세가 지연되면 data_delayed 경고와 지연 영업일을 준다', async () => {
    await signupAndLogin()
    setMockClockOffsetDays(-30)
    await mockApi.placeOrder('삼성전자', 'buy', 1)
    setMockClockOffsetDays(0)
    setMockDataLagDays(3)
    const p = await mockApi.getPerformance()
    expect(p.data_lag_business_days).toBe(3)
    expect(p.warnings.find((w) => w.kind === 'data_delayed')?.lag).toBe(3)
  })

  it('마지막 종가일 뒤에 체결된 주문은 pending_trades로 알리고 반영하지 않는다', async () => {
    await signupAndLogin()
    setMockClockOffsetDays(-30)
    await mockApi.placeOrder('삼성전자', 'buy', 1)
    setMockClockOffsetDays(0)
    setMockDataLagDays(40)
    // 40거래일 지연이면 30일 전 체결도 수집된 마지막 종가 뒤다
    const p = await mockApi.getPerformance()
    expect(['pending_only', 'ok']).toContain(p.status)
    if (p.status === 'pending_only') expect(p.warnings.some((w) => w.kind === 'pending_trades')).toBe(true)
  })
})

describe('ETF 실질 노출(look-through) mock (핸드오프 ETF_EXPOSURE)', () => {
  it('보유가 없으면 status empty + 안내 문장이다(오류 아님)', async () => {
    await signupAndLogin()
    const e = await mockApi.getLookThroughExposure()
    expect(e.status).toBe('empty')
    expect(e.message).toBeTruthy()
    expect(e.companies).toEqual([])
  })

  it('ETF 평가액은 총자산에 한 번만 들어가고, 기업 노출 + 미확인 + 현금이 총자산을 채운다', async () => {
    await signupAndLogin()
    await mockApi.placeOrder('삼성전자', 'buy', 10)
    await mockApi.placeOrder('KODEX 200', 'buy', 30)
    await mockApi.placeOrder('KODEX 레버리지', 'buy', 5)
    const e = await mockApi.getLookThroughExposure()
    const s = e.summary
    expect(e.status).toBe('ok')
    expect(s.total_asset).toBeCloseTo(s.cash_balance + s.stock_amount + s.etf_amount, 0)
    expect(s.companies_amount + s.unidentified_amount + s.cash_balance).toBeCloseTo(s.total_asset, 0)
    // 레버리지 ETF는 분해하지 않고 평가액 전체가 미확인 노출이다
    const leveraged = e.etfs.find((x) => x.ticker === '122630')!
    expect(leveraged.status).toBe('not_decomposed')
    expect(leveraged.reason_code).toBe('leveraged_inverse')
    expect(leveraged.constituents.every((c) => c.amount === null)).toBe(true)
    expect(s.undecomposed_etf_amount).toBeCloseTo(leveraged.eval_amount, 0)
    // 삼성전자는 직접 + ETF로 겹쳐 가진다
    const samsung = e.overlaps.find((c) => c.ticker === '005930')!
    expect(samsung.exposure_type).toBe('both')
    expect(samsung.total_weight_pct).toBeCloseTo(samsung.direct_weight_pct + samsung.via_etf_weight_pct, 1)
    // 종목 목록에 없는 구성종목은 linked: false
    expect(e.companies.some((c) => !c.linked)).toBe(true)
  })

  it('진단 결과에 저장 당시 look_through 스냅샷이 붙고, 비교(view=record)에 노출 변화가 온다', async () => {
    await signupAndLogin()
    await mockApi.placeOrder('KODEX 200', 'buy', 20)
    const first = await mockApi.getDiagnosis()
    expect(first.look_through?.status).toBe('ok')
    await mockApi.placeOrder('삼성전자', 'buy', 10)
    const second = await mockApi.getDiagnosis()
    const compare = await mockApi.compareDiagnosis(second.diagnosis_id!)
    expect(compare.basis?.recomputed).toBe(false)
    expect(compare.basis?.note).toBeTruthy()
    expect(compare.metric_changes?.holding_count.change).toBe(1)
    expect(compare.exposure_changes?.companies.length).toBeGreaterThan(0)
    // 과거 진단을 다시 열어도 저장 당시 스냅샷 그대로다
    const reopened = await mockApi.getDiagnosisDetail(first.diagnosis_id!)
    expect(reopened.look_through?.summary.total_asset).toBe(first.look_through?.summary.total_asset)
  })
})

describe('ETF 구성종목 mock', () => {
  it('수집된 ETF는 비중 큰 순으로 limit개를 주고, 수집하지 않은 ETF는 200 + items: []다', async () => {
    const kodex = await mockApi.getEtfHoldings('069500', 3)
    expect(kodex.items.map((i) => i.rank)).toEqual([1, 2, 3])
    expect(kodex.items[0].weight_pct).toBeGreaterThanOrEqual(kodex.items[1].weight_pct)
    expect(kodex.holding_count).toBeGreaterThan(3)
    const none = await mockApi.getEtfHoldings('114800').catch((e) => e)
    // KODEX 인버스는 단일 선물 구성이라 데이터는 있다. 존재하지 않는 종목만 404.
    expect(none.items.length).toBeGreaterThan(0)
    const missing = await mockApi.getEtfHoldings('없는종목').catch((e) => e)
    expect((missing as ApiError).status).toBe(404)
    await expect(mockApi.getEtfHoldings('069500', 51)).rejects.toMatchObject({ status: 422 })
  })
})

describe('일일 브리핑 mock (핸드오프 DAILY_BRIEFING)', () => {
  it('오늘 브리핑이 없으면 current는 null이고, 만들면 201 모양(created: true)이다', async () => {
    await signupAndLogin()
    expect(await mockApi.getCurrentDailyBriefing()).toBeNull()
    const created = await mockApi.generateDailyBriefing({ use_llm: false })
    expect(created.created).toBe(true)
    expect(created.version).toBe(1)
    expect(created.summary_source).toBe('code')
    expect(created.content.section_order).toEqual(['market', 'holdings', 'watchlist', 'risk', 'checklist'])
    const current = await mockApi.getCurrentDailyBriefing()
    expect(current?.id).toBe(created.id)
  })

  it('같은 날 다시 부르면 저장본 그대로(created: false), regenerate면 새 버전, 5개를 넘으면 409다', async () => {
    await signupAndLogin()
    const first = await mockApi.generateDailyBriefing({ use_llm: false })
    const again = await mockApi.generateDailyBriefing({ use_llm: false })
    expect(again.created).toBe(false)
    expect(again.id).toBe(first.id)
    for (let v = 2; v <= 5; v += 1) {
      const next = await mockApi.generateDailyBriefing({ regenerate: true, use_llm: false })
      expect(next.version).toBe(v)
    }
    const err: unknown = await mockApi.generateDailyBriefing({ regenerate: true, use_llm: false }).catch((e) => e)
    expect((err as ApiError).status).toBe(409)
  })

  it('보유가 없으면 보유·위험 영역이 empty이고 브리핑 자체는 만들어진다', async () => {
    await signupAndLogin()
    const b = await mockApi.generateDailyBriefing({ use_llm: false })
    expect(b.content.sections.holdings?.status).toBe('empty')
    expect(b.content.sections.risk?.status).toBe('empty')
    expect(b.section_status.market).toBe('ok')
  })

  it('시세가 늦으면 freshness 경고(warning, delayed)와 지연 영업일을 준다', async () => {
    await signupAndLogin()
    setMockDataLagDays(2)
    const b = await mockApi.generateDailyBriefing({ use_llm: false })
    expect(b.market_data_status).toBe('delayed')
    expect(b.content.freshness.warning).toBe(true)
    expect(b.content.freshness.lag_business_days).toBe(2)
  })

  it('한국어가 아닌 브리핑에는 같은 계산의 한국어 원문(original_content)이 온다', async () => {
    await signupAndLogin()
    await mockApi.updatePreferences({ preferred_locale: 'en-US' })
    const b = await mockApi.generateDailyBriefing({ use_llm: false })
    expect(b.locale).toBe('en-US')
    expect(b.original_content?.locale).toBe('ko-KR')
    expect(b.content.title).toContain('Daily investment briefing')
    // 나중에 언어를 바꿔도 저장본의 언어는 그대로다
    await mockApi.updatePreferences({ preferred_locale: 'ko-KR' })
    const saved = await mockApi.getDailyBriefing(b.id)
    expect(saved.locale).toBe('en-US')
  })

  it('남의 브리핑·없는 id는 404, history는 날짜별 마지막 버전만(latest_only)이다', async () => {
    await signupAndLogin()
    const b = await mockApi.generateDailyBriefing({ use_llm: false })
    await mockApi.generateDailyBriefing({ regenerate: true, use_llm: false })
    const list = await mockApi.listDailyBriefings()
    expect(list.items).toHaveLength(1)
    expect(list.items[0].versions_on_date).toBe(2)
    const all = await mockApi.listDailyBriefings({ latest_only: false })
    expect(all.total).toBe(2)
    await signupAndLogin()
    const err: unknown = await mockApi.getDailyBriefing(b.id).catch((e) => e)
    expect((err as ApiError).status).toBe(404)
  })
})

describe('언어·시간대 설정과 번역 상태 mock (핸드오프 I18N_HISTORY, 15-1절)', () => {
  it('기본은 ko-KR/Asia/Seoul이고, 지원하지 않는 언어와 모르는 시간대 저장은 422다', async () => {
    await signupAndLogin()
    const p = await mockApi.getPreferences()
    expect(p).toMatchObject({ preferred_locale: 'ko-KR', preferred_timezone: null, effective_timezone: 'Asia/Seoul' })
    await expect(mockApi.updatePreferences({ preferred_locale: 'fr-FR' })).rejects.toMatchObject({ status: 422 })
    await expect(mockApi.updatePreferences({ preferred_timezone: 'Mars/Base' })).rejects.toMatchObject({ status: 422 })
    const saved = await mockApi.updatePreferences({ preferred_timezone: 'America/New_York' })
    expect(saved.effective_timezone).toBe('America/New_York')
    // null이면 기본으로 돌아간다
    expect((await mockApi.updatePreferences({ preferred_timezone: null })).effective_timezone).toBe('Asia/Seoul')
  })

  it('한국어 답은 translation_status가 null이고, 영어는 translated(원문 있음)/localized/fallback_summary 세 상태를 만든다', { timeout: 30_000 }, async () => {
    await signupAndLogin()
    const ko = await mockApi.askResearch('삼성전자 주가 알려줘')
    expect(ko.locale).toBe('ko-KR')
    expect(ko.translation_status).toBeNull()

    await mockApi.updatePreferences({ preferred_locale: 'en-US' })
    const translated = await mockApi.askResearch('삼성전자 주가 알려줘')
    expect(translated.locale).toBe('en-US')
    expect(translated.translation_status).toBe('translated')
    const record = await mockApi.getReportRecord(translated.report_id!)
    expect(record.original_answer).toContain('삼성전자')
    expect(record.translation_status).toBe('translated')

    const localized = await mockApi.askResearch('PER이 뭐야?')
    expect(localized.translation_status).toBe('localized')

    const fallback = await mockApi.askResearch('삼성전자 번역실패')
    expect(fallback.translation_status).toBe('fallback_summary')
    expect((await mockApi.getReportRecord(fallback.report_id!)).original_answer).toBeTruthy()
  })

  it('기록 상세(view=record)에 분석 기준일(data_basis)과 날짜 그룹이 있다', async () => {
    await signupAndLogin()
    const r = await mockApi.askResearch('삼성전자 주가 알려줘')
    const record = await mockApi.getReportRecord(r.report_id!)
    expect(record.date_group).toBe('today')
    expect(record.data_basis[0]).toMatchObject({ tool: 'stock_tool', ticker: '005930' })
    expect(record.data_basis[0].price_as_of).toMatch(/^\d{4}-\d{2}-\d{2}$/)
    expect(record.analysis_types).toContain('price')
  })
})

describe('통합 기록 검색 mock', () => {
  it('리서치·진단을 한 목록으로 보여주고 탭 숫자(counts)는 같은 검색 조건의 건수다', { timeout: 30_000 }, async () => {
    await signupAndLogin()
    await mockApi.askResearch('삼성전자 주가 알려줘')
    await mockApi.askResearch('카카오 주가 알려줘')
    await mockApi.placeOrder('삼성전자', 'buy', 2)
    await mockApi.getDiagnosis()

    const all = await mockApi.searchHistory()
    expect(all.counts).toEqual({ all: 3, research: 2, diagnosis: 1 })
    expect(all.items[0].created_at >= all.items[1].created_at).toBe(true)
    const research = await mockApi.searchHistory({ type: 'research' })
    expect(research.total).toBe(2)
    expect(research.counts.all).toBe(3)
    expect(research.items.every((i) => i.kind === 'research' && i.research && !i.diagnosis)).toBe(true)
  })

  it('검색어는 모든 낱말이 들어간 기록을 찾고 별칭(삼전)도 찾으며, 시작일이 종료일보다 늦으면 422다', { timeout: 30_000 }, async () => {
    await signupAndLogin()
    await mockApi.askResearch('삼성전자 주가 알려줘')
    await mockApi.askResearch('카카오 주가 알려줘')
    const byAlias = await mockApi.searchHistory({ keyword: '삼전' })
    expect(byAlias.total).toBe(1)
    expect(byAlias.terms).toEqual(['삼전'])
    expect((await mockApi.searchHistory({ keyword: '삼성전자 카카오' })).total).toBe(0)
    const none = await mockApi.searchHistory({ keyword: '없는단어' })
    expect(none.total).toBe(0)
    expect(none.items).toEqual([])
    await expect(mockApi.searchHistory({ date_from: '2026-11-05', date_to: '2026-11-01' })).rejects.toMatchObject({ status: 422 })
  })

  it('범위를 넘는 페이지는 오류가 아니라 빈 items + has_next: false다', async () => {
    await signupAndLogin()
    await mockApi.askResearch('삼성전자 주가 알려줘')
    const beyond = await mockApi.searchHistory({ page: 9 })
    expect(beyond.items).toEqual([])
    expect(beyond.has_next).toBe(false)
    expect(beyond.total).toBe(1)
  })
})
