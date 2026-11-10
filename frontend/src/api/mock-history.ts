import type { HistoryItem, HistorySearchParams, HistorySearchResponse } from './contracts/history'
import type { HistoryApi } from './domains/history'
import { ApiError } from './errors'
import { currentUserId, storeFor, toRecordItem } from './mock'
import { DEFAULT_TIMEZONE, dateGroupOf, delay, isValidTimezone, localParts, mockNow } from './mock-core'

// GET /api/history/search mock (docs/FRONTEND_HANDOFF_I18N_HISTORY.md 3-2절): 리서치·진단 통합 검색.
// 검색어는 공백으로 나눈 낱말(최대 5개)이 "모두" 들어간 기록을 찾고, 별칭(삼전, 하닉)도 찾는다.
const ALIASES: Record<string, string> = { 삼전: '삼성전자', 하닉: 'SK하이닉스' }

function terms(keyword: string | undefined): string[] {
  return (keyword ?? '')
    .split(/\s+/)
    .map((t) => t.trim())
    .filter(Boolean)
    .slice(0, 5)
}

function matches(haystack: string, termList: string[]): boolean {
  const text = haystack.toLowerCase()
  return termList.every((t) => text.includes(t.toLowerCase()) || text.includes((ALIASES[t] ?? t).toLowerCase()))
}

async function searchHistory(params: HistorySearchParams = {}): Promise<HistorySearchResponse> {
  await delay(250)
  const store = storeFor(currentUserId())
  const timeZone = params.timezone ?? store.preferences.timezone ?? DEFAULT_TIMEZONE
  if (!isValidTimezone(timeZone)) throw new ApiError(`알 수 없는 시간대입니다: ${timeZone}`, 422)
  if (params.date_from && params.date_to && params.date_from > params.date_to) {
    throw new ApiError('date_from은 date_to보다 늦을 수 없습니다.', 422)
  }
  const type = params.type ?? 'all'
  const page = Math.max(1, params.page ?? 1)
  const size = Math.min(100, Math.max(1, params.size ?? 20))
  const sort = params.sort ?? 'created_at,desc'
  const keyword = params.keyword?.trim() || undefined
  const termList = terms(keyword)

  const research: HistoryItem[] = store.reportStore.map((report) => {
    const rec = toRecordItem(report, store)
    return {
      kind: 'research' as const,
      id: report.report_id,
      created_at: report.created_at,
      local_date: rec.local_date,
      local_time: rec.local_time,
      date_group: rec.date_group,
      title: rec.title,
      summary: report.summary,
      locale: rec.locale,
      company_names: rec.company_names,
      tickers: rec.tickers,
      detail_path: `/api/research/${report.report_id}`,
      research: {
        report_id: report.report_id,
        previous_report_id: report.previous_report_id,
        question: report.question,
        analysis_type: rec.analysis_type,
        analysis_types: rec.analysis_types,
        source_count: rec.source_count,
        used_tools: report.used_tools,
      },
      diagnosis: null,
    }
  })

  const diagnosis: HistoryItem[] = store.diagnosisHistory.map((d) => {
    const local = localParts(d.created_at!, timeZone)
    const names = d.holdings.map((h) => h.company_name)
    const first = names[0] ?? ''
    const locale = store.preferences.locale
    return {
      kind: 'diagnosis' as const,
      id: d.id,
      created_at: d.created_at!,
      local_date: local.date,
      local_time: local.time,
      date_group: dateGroupOf(local.date, timeZone, mockNow()),
      title:
        locale === 'ko-KR'
          ? names.length > 1
            ? `${first} 외 ${names.length - 1}종목 진단`
            : `${first} 진단`
          : names.length > 1
            ? `Diagnosis: ${first} and ${names.length - 1} more`
            : `Diagnosis: ${first}`,
      summary: d.summary,
      locale,
      company_names: names,
      tickers: d.holdings.map((h) => h.ticker),
      detail_path: `/api/portfolio/diagnosis/${d.id}`,
      research: null,
      diagnosis: {
        diagnosis_id: d.id,
        source: d.source,
        total_asset: d.metrics.total_asset,
        holdings_count: d.holdings.length,
        cash_weight_pct: d.metrics.cash_weight_pct,
        top_holdings: [...d.holdings]
          .sort((a, b) => (b.weight_pct ?? 0) - (a.weight_pct ?? 0))
          .slice(0, 3)
          .map((h) => ({ ticker: h.ticker, company_name: h.company_name, weight_pct: h.weight_pct })),
        risk_summary: d.risks[0] ?? null,
        flags: d.flags.map((f) => ({ code: f.code, ticker: f.ticker, company_name: f.company_name, market: f.market })),
        flag_codes: d.flags.map((f) => f.code),
        generated_at: d.generated_at,
      },
    }
  })

  const haystackOf = (item: HistoryItem): string => {
    if (item.kind === 'research') {
      const report = store.reportStore.find((r) => r.report_id === item.id)
      return [item.title, item.summary, report?.answer, ...item.company_names, ...item.tickers].join(' ')
    }
    const d = store.diagnosisHistory.find((x) => x.id === item.id)
    return [
      item.summary,
      ...(d?.strengths ?? []),
      ...(d?.risks ?? []),
      ...(d?.suggestions ?? []),
      ...(d?.flags.map((f) => f.message) ?? []),
      ...item.company_names,
      ...item.tickers,
      // 위험 키워드는 플래그로도 찾는다(집중도, 위험, 현금 ...)
      ...(d?.flags.some((f) => f.code === 'CONCENTRATION_HIGH' || f.code === 'MARKET_SKEW') ? ['집중도'] : []),
      ...(d && d.flags.length > 0 ? ['위험'] : []),
    ].join(' ')
  }

  const inRange = (item: HistoryItem) =>
    (!params.date_from || item.local_date >= params.date_from) && (!params.date_to || item.local_date <= params.date_to)
  const matched = [...research, ...diagnosis].filter((item) => inRange(item) && (termList.length === 0 || matches(haystackOf(item), termList)))

  const counts = {
    all: matched.length,
    research: matched.filter((i) => i.kind === 'research').length,
    diagnosis: matched.filter((i) => i.kind === 'diagnosis').length,
  }
  const direction = sort === 'created_at,asc' ? 1 : -1
  const selected = matched
    .filter((i) => type === 'all' || i.kind === type)
    .sort((a, b) => direction * (a.created_at.localeCompare(b.created_at) || a.id - b.id))
  const items = selected.slice((page - 1) * size, page * size)

  return {
    type,
    keyword: keyword ?? null,
    terms: termList,
    date_from: params.date_from ?? null,
    date_to: params.date_to ?? null,
    timezone: timeZone,
    sort,
    page,
    size,
    total: selected.length,
    total_pages: Math.max(1, Math.ceil(selected.length / size)),
    has_next: page * size < selected.length,
    counts,
    items,
  }
}

export const historyMock: HistoryApi = { searchHistory }
