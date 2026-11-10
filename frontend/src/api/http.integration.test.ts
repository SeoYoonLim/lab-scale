import { beforeAll, describe, expect, it } from 'vitest'
import { ApiError } from './errors'
import { httpApi } from './http'
import { clearToken, setToken } from '../utils/token'

// 실제 백엔드에 붙여 보는 통합 테스트(기본은 건너뜀). 모든 응답은 httpApi가 zod 스키마로 검증하므로, 서버 응답이 우리가 기대한
// 모양(backend/API.md와 핸드오프 문서)과 다르면 ApiError(502, 응답 형식 오류)로 여기서 먼저 걸린다.
//
//   1) 백엔드를 띄운다(DB에 종목·시세가 있어야 의미 있는 응답이 온다. Ollama는 없어도 된다 - 코드 요약/대체 답으로 동작)
//   2) cd frontend && VITE_REAL_BACKEND=1 VITE_API_BASE_URL=http://127.0.0.1:8000/api npx vitest run src/api/http.integration.test.ts
//
// 가입한 임시 계정은 매번 새로 만든다. 서버 데이터는 건드리지 않고 그 계정의 주문·기록만 만든다.
const enabled = import.meta.env.VITE_REAL_BACKEND === '1'
const TIMEOUT = 120_000

describe.skipIf(!enabled)('실제 백엔드 통합(httpApi + zod 검증)', () => {
  beforeAll(async () => {
    clearToken()
    const username = `it${Math.random().toString(36).slice(2, 10)}`
    const res = await httpApi.signup(username, 'Zk9-verify-pw')
    setToken(res.access_token)
  }, TIMEOUT)

  it('공개 API: 상태·면책·종목·급등락·시세 차트·사건 반응·ETF 구성종목', { timeout: TIMEOUT }, async () => {
    const status = await httpApi.getStatus()
    expect(status.is_realtime).toBe(false)
    expect(await httpApi.getDisclaimer()).toContain('모의투자')

    const companies = await httpApi.searchCompanies('삼성', 5)
    expect(companies.length).toBeGreaterThan(0)
    for (const category of ['gainers', 'losers', 'volume_surge'] as const) {
      const t = await httpApi.getTrending(category, 3)
      expect(t.category).toBe(category)
    }

    const history = await httpApi.getPriceHistory('005930', 30)
    expect(history.count).toBe(history.items.length)
    expect(history.items[0].date <= history.items.at(-1)!.date).toBe(true)
    const reactions = await httpApi.getEventReactions('005930')
    expect(reactions.ticker).toBe('005930')

    const etf = await httpApi.getEtfHoldings('069500', 5)
    expect(etf.items.length).toBeGreaterThan(0)
    // 수집하지 않은 ETF는 200 + items: [] + message
    const none = await httpApi.getEtfHoldings('360750', 5)
    expect(none.items).toEqual([])
    expect(none.message).toBeTruthy()
    await expect(httpApi.getEtfHoldings('없는종목123')).rejects.toMatchObject({ status: 404 })
  })

  it('계정: 내 정보·언어/시간대 설정(저장·초기화)·지원하지 않는 값은 422', { timeout: TIMEOUT }, async () => {
    const me = await httpApi.me()
    expect(me.username).toBeTruthy()
    const prefs = await httpApi.getPreferences()
    expect(prefs.preferred_locale).toBe('ko-KR')
    expect(prefs.supported_locales).toContain('en-US')

    const saved = await httpApi.updatePreferences({ preferred_locale: 'en-US', preferred_timezone: 'America/New_York' })
    expect(saved.effective_timezone).toBe('America/New_York')
    await expect(httpApi.updatePreferences({ preferred_locale: 'fr-FR' })).rejects.toMatchObject({ status: 422 })
    const reset = await httpApi.updatePreferences({ preferred_locale: 'ko-KR', preferred_timezone: null })
    expect(reset.effective_timezone).toBe('Asia/Seoul')
  })

  it('포트폴리오: 빈 상태의 성과·노출 -> 주문 -> 성과·노출·진단(look_through)·이력·비교', { timeout: TIMEOUT }, async () => {
    // 새 사용자의 첫 GET /api/portfolio가 계좌를 만든다
    const empty = await httpApi.getPortfolio()
    expect(empty.holdings).toEqual([])

    const emptyPerf = await httpApi.getPerformance({ period: '3m', benchmark: 'KS11' })
    expect(['no_trades', 'no_account']).toContain(emptyPerf.status)
    expect(emptyPerf.summary.period_return_pct).toBeNull()
    const emptyExposure = await httpApi.getLookThroughExposure({ limit: 5 })
    expect(emptyExposure.status).toBe('empty')

    for (const [ticker, quantity] of [['삼성전자', 5], ['KODEX 200', 20], ['KODEX 레버리지', 3]] as const) {
      const order = await httpApi.placeOrder(ticker, 'buy', quantity)
      expect(order.quantity).toBe(quantity)
      expect(['stock', 'etf', null, undefined]).toContain(order.asset_type)
    }
    // 엄격한 종목 매칭: 음차·오타는 체결하지 않고 404
    await expect(httpApi.placeOrder('삼성전쟈', 'buy', 1)).rejects.toMatchObject({ status: 404 })

    const trades = await httpApi.getTrades(10, 0)
    expect(trades.total).toBe(3)
    expect(trades.total_pages).toBeGreaterThanOrEqual(1)

    for (const period of ['1m', '3m', '6m', '1y', 'all'] as const) {
      const perf = await httpApi.getPerformance({ period, benchmark: 'KQ11' })
      // 오늘 체결한 주문은 다음 종가가 수집되기 전까지 pending_only일 수 있다 - 어느 상태든 모양은 같아야 한다
      expect(['ok', 'partial', 'pending_only', 'no_prices', 'history_unavailable']).toContain(perf.status)
      if (perf.status === 'ok' || perf.status === 'partial') expect(perf.series.points.length).toBeGreaterThan(0)
      else expect(perf.summary.period_return_pct).toBeNull()
    }

    const exposure = await httpApi.getLookThroughExposure({ limit: 10 })
    expect(exposure.status).toBe('ok')
    expect(exposure.summary.total_asset).toBeGreaterThan(0)
    const leveraged = exposure.etfs.find((e) => e.ticker === '122630')
    expect(leveraged?.status).toBe('not_decomposed')
    expect(exposure.companies.length).toBeGreaterThan(0)

    // Ollama가 없으면 진단은 규칙 기반 문장으로 폴백한다(200)
    const diagnosis = await httpApi.getDiagnosis()
    expect(diagnosis.diagnosis_id).not.toBeNull()
    expect(diagnosis.look_through?.status).toBe('ok')
    const detail = await httpApi.getDiagnosisDetail(diagnosis.diagnosis_id!)
    expect(detail.look_through?.summary.total_asset).toBe(diagnosis.look_through?.summary.total_asset)

    const second = await httpApi.getDiagnosis()
    const compare = await httpApi.compareDiagnosis(second.diagnosis_id!)
    expect(compare.basis?.recomputed).toBe(false)
    expect(compare.metric_changes).toBeTruthy()
    const list = await httpApi.getDiagnosisHistory(10, 0)
    expect(list.total).toBeGreaterThanOrEqual(2)
    await httpApi.deleteDiagnosis(diagnosis.diagnosis_id!)
  })

  it('일일 브리핑: 없음(null) -> 만들기 -> 저장본 -> 새 버전 -> 이력·상세', { timeout: TIMEOUT }, async () => {
    expect(await httpApi.getCurrentDailyBriefing()).toBeNull()
    const created = await httpApi.generateDailyBriefing({ use_llm: false })
    expect(created.created).toBe(true)
    expect(created.content.section_order).toEqual(['market', 'holdings', 'watchlist', 'risk', 'checklist'])
    const again = await httpApi.generateDailyBriefing({ use_llm: false })
    expect(again.created).toBe(false)
    expect(again.id).toBe(created.id)
    const next = await httpApi.generateDailyBriefing({ regenerate: true, use_llm: false })
    expect(next.version).toBe(2)
    const current = await httpApi.getCurrentDailyBriefing()
    expect(current?.id).toBe(next.id)
    const list = await httpApi.listDailyBriefings({ page: 1, size: 5 })
    expect(list.items[0].versions_on_date).toBe(2)
    const detail = await httpApi.getDailyBriefing(created.id)
    expect(detail.version).toBe(1)
    await expect(httpApi.getDailyBriefing(999_999_999)).rejects.toMatchObject({ status: 404 })
  })

  it('리서치·기록: 질문(번역 필드) -> 기록 상세(view=record) -> 통합 검색', { timeout: TIMEOUT }, async () => {
    // Ollama가 없어도 대개 200 + 대체 답이다. 서버가 502/503으로 답하면 그 사실만 확인하고 넘어간다.
    const answered: Awaited<ReturnType<typeof httpApi.askResearch>> | ApiError = await httpApi
      .askResearch('삼성전자 현재가 알려줘')
      .catch((e: unknown) => (e instanceof ApiError ? e : Promise.reject(e)))
    if (answered instanceof ApiError) {
      expect([502, 503]).toContain(answered.status)
    } else {
      expect(answered.locale ?? 'ko-KR').toBe('ko-KR')
      expect(answered.translation_status ?? null).toBeNull()
      if (answered.report_id != null) {
        const record = await httpApi.getReportRecord(answered.report_id)
        expect(record.report_id).toBe(answered.report_id)
        expect(record.date_group).toBeTruthy()
      }
    }
    const search = await httpApi.searchHistory({ type: 'all', size: 5 })
    expect(search.counts.all).toBe(search.counts.research + search.counts.diagnosis)
    expect(search.counts.diagnosis).toBeGreaterThanOrEqual(1)
    const diag = await httpApi.searchHistory({ type: 'diagnosis', keyword: '삼성전자' })
    expect(diag.items.every((i) => i.kind === 'diagnosis')).toBe(true)
    await expect(httpApi.searchHistory({ date_from: '2026-11-05', date_to: '2026-11-01' })).rejects.toMatchObject({ status: 422 })
  })

  it('로그아웃하면 이 토큰은 더 쓸 수 없다(401)', { timeout: TIMEOUT }, async () => {
    await httpApi.logout()
    const err: unknown = await httpApi.me().catch((e) => e)
    expect((err as ApiError).status).toBe(401)
  })
})
