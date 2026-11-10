import type { Locale } from './contracts/common'
import type {
  BriefingContent,
  BriefingDisclosure,
  BriefingFlag,
  BriefingHistoryParams,
  BriefingNewsItem,
  BriefingSource,
  ChecklistItem,
  DailyBriefing,
  DailyBriefingHistory,
  EtfExposureBrief,
  GenerateBriefingInput,
  HoldingBriefingItem,
  MarketIndexQuote,
  WatchlistBriefingItem,
} from './contracts/briefing'
import type { MarketDataStatus, SectionStatus } from './contracts/common'
import type { BriefingApi } from './domains/briefing'
import { ApiError } from './errors'
import { currentUserId, storeFor } from './mock'
import {
  DEFAULT_TIMEZONE,
  collectedSeries,
  dateGroupOf,
  delay,
  expectedTradingDate,
  findCompanyMeta,
  getPriceSeries,
  isValidTimezone,
  isLeveragedOrInverseEtf,
  localParts,
  mockDataLagDays,
  mockNow,
  syntheticIndexCloses,
} from './mock-core'
import { computeLookThrough } from './mock-exposure-core'
import { toLocale, translate } from '../i18n'

// 개인화 일일 투자 브리핑 mock (docs/FRONTEND_HANDOFF_DAILY_BRIEFING.md).
// 보유·관심종목·시세로 5개 영역(시장/보유/관심/위험/확인할 사항)을 코드가 계산한 것처럼 만든다. 뉴스·공시 제목은 샘플이다.
// 저장본은 불변 스냅샷이라(다시 계산하지 않음) 한 번 만든 브리핑은 그대로 보관한다.
const MAX_VERSIONS_PER_DAY = 5

interface BriefingState {
  items: DailyBriefing[]
  nextId: number
}

const states = new Map<number, BriefingState>()

function stateFor(userId: number): BriefingState {
  let s = states.get(userId)
  if (!s) {
    s = { items: [], nextId: 1 }
    states.set(userId, s)
  }
  return s
}

type Tx = (ko: string, en: string) => string
const txFor = (locale: Locale): Tx => (ko, en) => (locale === 'ko-KR' ? ko : en)
const round2 = (n: number) => Math.round(n * 100) / 100
const pct = (n: number | null) => (n == null ? '-' : `${n > 0 ? '+' : ''}${n.toFixed(2)}%`)
const won = (n: number) => `${Math.round(n).toLocaleString('ko-KR')}원`

function marketStatus(lag: number): MarketDataStatus {
  if (lag <= 0) return 'latest'
  return lag <= 5 ? 'delayed' : 'stale'
}

interface Quote {
  date: string
  close: number
  dayChange: number | null
  weekChange: number | null
  return20: number | null
  volumeRatio: number | null
}

function quoteOf(ticker: string): Quote | null {
  const series = collectedSeries(ticker)
  const last = series.at(-1)
  if (!last) return null
  const prev = series.at(-2)
  const week = series.at(-6)
  const month = series.at(-21)
  const change = (a: number, b?: number) => (b ? round2((a / b - 1) * 100) : null)
  const volumes = series.slice(-21, -1).map((p) => p.volume ?? 0)
  const avgVolume = volumes.length > 0 ? volumes.reduce((s, v) => s + v, 0) / volumes.length : 0
  return {
    date: last.price_date,
    close: last.close_price,
    dayChange: change(last.close_price, prev?.close_price),
    weekChange: change(last.close_price, week?.close_price),
    return20: change(last.close_price, month?.close_price),
    volumeRatio: avgVolume > 0 && last.volume ? round2(last.volume / avgVolume) : null,
  }
}

function sampleNews(name: string, count: number, tx: Tx, date: string): BriefingNewsItem[] {
  return Array.from({ length: count }, (_, i) => ({
    url: `https://search.naver.com/search.naver?where=news&query=${encodeURIComponent(name)}`,
    title: tx(`[샘플] ${name} 관련 뉴스 ${i + 1}`, `[Sample] News about ${name} ${i + 1}`),
    source: 'sample.news',
    published_at: `${date}T1${i}:20:00+09:00`,
    similar_count: i === 0 ? 3 : 1,
  }))
}

function sampleDisclosure(name: string, date: string): BriefingDisclosure {
  return {
    title: `[샘플] ${name} 주요사항보고서`,
    source_url: 'https://dart.fss.or.kr',
    // 공시는 날짜만 아는 값이라 T00:00:00+09:00로 온다(핸드오프 15-3절) - 화면은 시각 없이 KST 날짜만 쓴다.
    disclosed_at: `${date}T00:00:00+09:00`,
    disclosure_type: '주요사항보고서',
  }
}

function buildContent(locale: Locale, localDate: string, timezone: string, userId: number, useLlm: boolean): BriefingContent {
  const tx = txFor(locale)
  const store = storeFor(userId)
  const lag = mockDataLagDays()
  const expected = expectedTradingDate()
  const dataAsOf = collectedSeries('005930').at(-1)?.price_date ?? null
  const status = marketStatus(lag)
  const generatedAt = mockNow().toISOString()

  // ---- 시장 ----
  const len = getPriceSeries('005930').length
  const indexDefs = [
    { code: 'kospi', name: tx('코스피', 'KOSPI'), base: 2700, seed: 11, unit: '' },
    { code: 'kosdaq', name: tx('코스닥', 'KOSDAQ'), base: 850, seed: 22, unit: '' },
    { code: 'usd_krw', name: tx('원/달러', 'USD/KRW'), base: 1330, seed: 33, unit: tx('원', ' KRW') },
  ] as const
  const dates = collectedSeries('005930').map((p) => p.price_date)
  const indices: MarketIndexQuote[] = indexDefs.map((def) => {
    const closes = syntheticIndexCloses(def.base, def.seed, len, def.code === 'usd_krw' ? 0.008 : 0.03).slice(0, dates.length)
    const close = closes.at(-1)!
    const dayChange = closes.length > 1 ? round2((close / closes.at(-2)! - 1) * 100) : null
    const weekChange = closes.length > 5 ? round2((close / closes.at(-6)! - 1) * 100) : null
    return {
      code: def.code,
      name: def.name,
      date: dates.at(-1) ?? null,
      close,
      change_pct: dayChange,
      week_change_pct: weekChange,
      lag_business_days: lag,
      text: tx(
        `${def.name} ${dates.at(-1)} 종가 ${close.toLocaleString('ko-KR')}${def.unit} · 전일 대비 ${pct(dayChange)} · 5거래일 ${pct(weekChange)}`,
        `${def.name} close on ${dates.at(-1)}: ${close.toLocaleString('en-US')}${def.unit} · day ${pct(dayChange)} · 5 days ${pct(weekChange)}`,
      ),
    }
  })

  // ---- 보유 ----
  const holdingsRaw = [...store.holdings].map(([ticker, h]) => ({ ticker, ...h }))
  const priced = holdingsRaw.flatMap((h) => {
    const meta = findCompanyMeta(h.ticker)
    const quote = quoteOf(h.ticker)
    return meta && quote ? [{ h, meta, quote, eval: quote.close * h.quantity }] : []
  })
  const holdingsValue = priced.reduce((s, p) => s + p.eval, 0)
  const totalAsset = store.cashBalance + holdingsValue

  const holdingItems: HoldingBriefingItem[] = priced
    .sort((a, b) => b.eval - a.eval)
    .map(({ h, meta, quote, eval: evalAmount }) => {
      const weight = totalAsset > 0 ? round2((evalAmount / totalAsset) * 100) : null
      const profitPct = round2((quote.close / h.avg_price - 1) * 100)
      const riskFactors: string[] = []
      if (quote.dayChange != null && Math.abs(quote.dayChange) >= 3) {
        riskFactors.push(
          tx(
            `${meta.name}의 ${quote.date} 종가는 전 거래일 대비 ${pct(quote.dayChange)} ${quote.dayChange < 0 ? '하락' : '상승'}했습니다.`,
            `${meta.name}'s close on ${quote.date} moved ${pct(quote.dayChange)} from the previous trading day.`,
          ),
        )
      }
      if (isLeveragedOrInverseEtf(meta.name)) {
        riskFactors.push(
          tx(
            `${meta.name}은(는) 레버리지·인버스 ETF라 오래 보유하면 기초지수 수익률과 달라질 수 있습니다.`,
            `${meta.name} is a leveraged/inverse ETF; holding it for long can diverge from the index return.`,
          ),
        )
      }
      if (quote.volumeRatio != null && quote.volumeRatio >= 2) {
        riskFactors.push(tx(`${meta.name}의 거래량이 평균의 ${quote.volumeRatio}배입니다.`, `${meta.name}'s volume is ${quote.volumeRatio}x the average.`))
      }
      const isSemiconductor = meta.ticker === '005930' || meta.ticker === '000660'
      return {
        ticker: meta.ticker,
        name: meta.name,
        asset_type: meta.assetType,
        latest_date: quote.date,
        lag_business_days: lag,
        latest_close: quote.close,
        day_change_pct: quote.dayChange,
        week_change_pct: quote.weekChange,
        return_20d_pct: quote.return20,
        volume_ratio: quote.volumeRatio,
        quantity: h.quantity,
        avg_price: round2(h.avg_price),
        eval_amount: evalAmount,
        weight_pct: weight,
        profit_loss_pct: profitPct,
        price_text: tx(
          `${quote.date} 종가 ${won(quote.close)} · 전일 대비 ${pct(quote.dayChange)} · 5거래일 ${pct(quote.weekChange)} · 20거래일 ${pct(quote.return20)}`,
          `Close on ${quote.date}: KRW ${Math.round(quote.close).toLocaleString('en-US')} · day ${pct(quote.dayChange)} · 5 days ${pct(quote.weekChange)} · 20 days ${pct(quote.return20)}`,
        ),
        position_text: tx(
          `보유 ${h.quantity}주 · 평가액 ${won(evalAmount)} · 비중 ${weight}% · 평균 매입가 대비 ${pct(profitPct)}`,
          `${h.quantity} shares · value KRW ${Math.round(evalAmount).toLocaleString('en-US')} · weight ${weight}% · vs. average cost ${pct(profitPct)}`,
        ),
        risk_factors: riskFactors,
        notes: [],
        news_label: tx('직접 관련 뉴스', 'Directly related news'),
        news: sampleNews(meta.name, 2, tx, quote.date),
        news_empty_text: null,
        indirect_news: isSemiconductor
          ? {
              kind: 'sector',
              label: tx('업종·산업 뉴스(간접)', 'Sector / industry news (indirect)'),
              topic: tx('반도체', 'Semiconductors'),
              // 간접 뉴스(업종·산업)는 UTC(+00:00)로 온다 - 직접 뉴스(+09:00)와 형식이 다르다(핸드오프 15-3절).
              items: [
                {
                  url: 'https://search.naver.com/search.naver?where=news&query=반도체',
                  title: tx('[샘플] 반도체 업황 관련 기사', '[Sample] Semiconductor industry article'),
                  source: 'sample.news',
                  published_at: `${quote.date}T01:36:00+00:00`,
                  similar_count: 1,
                },
              ],
            }
          : null,
        disclosures: meta.assetType === 'stock' ? [sampleDisclosure(meta.name, quote.date)] : [],
        disclosure_empty_text:
          meta.assetType === 'etf'
            ? tx('ETF는 공시를 수집하지 않습니다.', 'Disclosures are not collected for ETFs.')
            : tx('최근 7일 공시가 없습니다.', 'No filings in the last 7 days.'),
      }
    })

  // ---- 관심 ----
  const holdingTickers = new Set(holdingsRaw.map((h) => h.ticker))
  const watchRaw = store.watchlist.filter((w) => !holdingTickers.has(w.ticker))
  const watchItems: WatchlistBriefingItem[] = watchRaw.flatMap((w) => {
    const meta = findCompanyMeta(w.ticker)
    const quote = quoteOf(w.ticker)
    if (!meta || !quote) return []
    const notable = quote.dayChange != null && Math.abs(quote.dayChange) >= 2
    const researched = store.reportStore.filter((r) => r.company_name === meta.name).at(-1)
    return [
      {
        ticker: meta.ticker,
        name: meta.name,
        latest_date: quote.date,
        day_change_pct: quote.dayChange,
        week_change_pct: quote.weekChange,
        price_text: tx(
          `${quote.date} 종가 ${won(quote.close)} · 전일 대비 ${pct(quote.dayChange)} · 5거래일 ${pct(quote.weekChange)}`,
          `Close on ${quote.date}: KRW ${Math.round(quote.close).toLocaleString('en-US')} · day ${pct(quote.dayChange)} · 5 days ${pct(quote.weekChange)}`,
        ),
        notable,
        changes: notable ? [tx(`${meta.name}이(가) 전일 대비 ${pct(quote.dayChange)} 움직였습니다.`, `${meta.name} moved ${pct(quote.dayChange)} from the previous day.`)] : [],
        status_text: notable ? null : tx('특별한 업데이트가 없습니다.', 'No notable update.'),
        since_research: researched
          ? {
              last_research_at: researched.created_at,
              news_after: 2,
              disclosures_after: 1,
              text: tx(`마지막 리서치(#${researched.report_id}) 이후 새 뉴스 2건, 공시 1건이 있습니다.`, `Since your last research (#${researched.report_id}): 2 new news items and 1 filing.`),
            }
          : null,
        disclosures: meta.assetType === 'stock' && notable ? [sampleDisclosure(meta.name, quote.date)] : [],
        news: notable ? sampleNews(meta.name, 1, tx, quote.date) : [],
      },
    ]
  })
  watchItems.sort((a, b) => Number(b.notable) - Number(a.notable))
  const overlap = store.watchlist.length - watchRaw.length

  // ---- 위험 ----
  const sorted = priced.map((p) => p.eval).sort((a, b) => b - a)
  const top1 = totalAsset > 0 && sorted[0] ? round2((sorted[0] / totalAsset) * 100) : null
  const top3 = totalAsset > 0 && sorted.length > 0 ? round2((sorted.slice(0, 3).reduce((s, v) => s + v, 0) / totalAsset) * 100) : null
  const cashWeight = totalAsset > 0 ? round2((store.cashBalance / totalAsset) * 100) : null
  const herfindahl = holdingsValue > 0 ? round2(priced.reduce((s, p) => s + (p.eval / holdingsValue) ** 2, 0)) : null
  // 시장별 비중은 주식 평가금액 대비이고 ETF는 뺀다(진단과 같은 정의)
  const stockEvalByMarket = new Map<string, number>()
  for (const p of priced.filter((x) => x.meta.assetType === 'stock')) {
    stockEvalByMarket.set(p.meta.market!, (stockEvalByMarket.get(p.meta.market!) ?? 0) + p.eval)
  }
  const stockValue = [...stockEvalByMarket.values()].reduce((s, v) => s + v, 0)
  const marketWeights: Record<string, number | null> = Object.fromEntries(
    [...stockEvalByMarket].map(([market, value]) => [market, round2((value / stockValue) * 100)]),
  )

  const riskFlags: BriefingFlag[] = []
  if (cashWeight != null && cashWeight >= 70) {
    riskFlags.push({ code: 'CASH_HIGH', text: tx(`현금 비중이 ${cashWeight}%로 기준(70%) 이상입니다.`, `Cash is ${cashWeight}% of total assets, above the 70% threshold.`), value: cashWeight, threshold: 70, ticker: null, company_name: null })
  }
  if (top1 != null && top1 >= 40) {
    riskFlags.push({ code: 'CONCENTRATION_HIGH', text: tx(`상위 1종목 비중이 ${top1}%로 기준(40%) 이상입니다.`, `The top holding is ${top1}% of total assets, above the 40% threshold.`), value: top1, threshold: 40, ticker: null, company_name: null })
  }

  const hasEtf = priced.some((p) => p.meta.assetType === 'etf')
  let etfExposure: EtfExposureBrief
  if (!hasEtf) {
    etfExposure = { status: 'no_etf', text: tx('보유한 ETF가 없습니다.', 'You hold no ETFs.'), flags: [], top_companies: [], undecomposed: [] }
  } else {
    const view = computeLookThrough({
      cash: store.cashBalance,
      holdings: holdingsRaw.map((h) => ({ ticker: h.ticker, quantity: h.quantity })),
      priceOf: (ticker) => {
        const last = collectedSeries(ticker).at(-1)
        return last ? { price: last.close_price, date: last.price_date } : null
      },
      limit: 3,
      locale,
      expectedDate: expected,
      lagBusinessDays: lag,
      tradingDates: dates,
    })
    etfExposure = {
      status: 'computed',
      text: tx(
        `ETF 구성종목 비중을 반영한 기업별 실질 노출을 계산했습니다(구성종목 기준일 ${view.etfs.find((e) => e.holdings_as_of)?.holdings_as_of ?? '-'}).`,
        `Look-through exposure per company was calculated from ETF constituent weights (constituents as of ${view.etfs.find((e) => e.holdings_as_of)?.holdings_as_of ?? '-'}).`,
      ),
      flags: view.flags.map((f) => ({ kind: f.kind, text: f.message, value_pct: f.value_pct ?? null, threshold_pct: f.threshold_pct ?? null })),
      top_companies: view.companies.map((c) => ({ code: c.code, name: c.name, weight_pct: c.total_weight_pct })),
      undecomposed: view.etfs.filter((e) => e.status === 'not_decomposed').map((e) => ({ ticker: e.ticker, name: e.name })),
    }
  }

  const cautions: string[] = []
  if (lag > 0) {
    cautions.push(tx(`시세가 ${lag}영업일 지연돼 위험 점검도 ${dataAsOf} 종가 기준입니다.`, `Prices are ${lag} business days behind, so the risk check is also based on the close of ${dataAsOf}.`))
  }
  for (const item of holdingItems) cautions.push(...item.risk_factors.filter((r) => /%/.test(r)))

  // ---- 확인할 사항(최대 3개, 우선순위: 지연 -> 보유 공시 -> 위험 -> 관심 공시) ----
  const checklist: ChecklistItem[] = []
  if (lag > 0) {
    checklist.push({
      kind: 'data_delay',
      text: tx(`시세가 ${lag}영업일 지연돼 있어 최신 거래일(${expected}) 내용이 반영되지 않았습니다.`, `Prices are ${lag} business days behind, so the latest trading day (${expected}) is not reflected.`),
      ticker: null,
      url: null,
    })
  }
  for (const item of holdingItems) {
    const d = item.disclosures[0]
    if (d) {
      checklist.push({ kind: 'holding_disclosure', text: tx(`${item.name}(${item.ticker})의 최근 공시: ${d.title}`, `Recent filing for ${item.name} (${item.ticker}): ${d.title}`), ticker: item.ticker, url: d.source_url })
    }
  }
  for (const f of riskFlags) checklist.push({ kind: f.code === 'CASH_HIGH' ? 'risk_cash_high' : 'risk_concentration_high', text: f.text, ticker: null, url: null })
  for (const item of watchItems) {
    const d = item.disclosures[0]
    if (d) checklist.push({ kind: 'watchlist_disclosure', text: tx(`${item.name}(${item.ticker})의 최근 공시: ${d.title}`, `Recent filing for ${item.name} (${item.ticker}): ${d.title}`), ticker: item.ticker, url: d.source_url })
  }
  const checklistItems = checklist.slice(0, 3)

  // ---- 상태·신선도·요약 ----
  const holdingsStatus: SectionStatus = holdingItems.length > 0 ? 'ok' : 'empty'
  const watchStatus: SectionStatus = watchItems.length > 0 ? 'ok' : 'empty'
  const riskStatus: SectionStatus = holdingItems.length > 0 ? 'ok' : 'empty'
  const checklistStatus: SectionStatus = checklistItems.length > 0 ? 'ok' : 'empty'
  const sectionStatus: Record<string, SectionStatus> = {
    market: 'ok',
    holdings: holdingsStatus,
    watchlist: watchStatus,
    risk: riskStatus,
    checklist: checklistStatus,
  }

  const headline =
    status === 'latest'
      ? tx(`${dataAsOf} 종가 기준 최신 시세입니다.`, `Prices are the latest close (${dataAsOf}).`)
      : tx(
          `시세가 ${lag}영업일 지연됐습니다. 마지막 종가는 ${dataAsOf}, 기대 거래일은 ${expected}입니다(미수집). 아래 숫자는 ${dataAsOf} 종가 기준이며 오늘의 시장 상황이 아닙니다.`,
          `Prices are ${lag} business days behind. The last close is ${dataAsOf}; the expected trading day is ${expected}. The numbers below are based on the ${dataAsOf} close and do not reflect today's market.`,
        )
  const summaryText = [
    lag > 0 ? tx(`시세가 ${lag}영업일 지연된 ${dataAsOf} 기준입니다.`, `Based on the close of ${dataAsOf}, ${lag} business days behind.`) : tx(`${dataAsOf} 종가 기준입니다.`, `Based on the close of ${dataAsOf}.`),
    ...indices.slice(0, 2).map((i) => i.text),
    ...holdingItems.slice(0, 3).map((i) => i.price_text ? `${i.name}: ${i.price_text}` : i.name),
  ].join(' ')

  return {
    title: tx(`${localDate} 일일 투자 브리핑`, `${localDate} Daily investment briefing`),
    locale,
    local_date: localDate,
    timezone,
    generated_at: generatedAt,
    data_as_of: dataAsOf,
    market_data_status: status,
    section_order: ['market', 'holdings', 'watchlist', 'risk', 'checklist'],
    section_status: sectionStatus,
    freshness: {
      status,
      warning: status !== 'latest',
      headline,
      notices:
        lag > 0
          ? [
              tx(`코스피 지수 데이터는 ${dataAsOf} 기준입니다(${lag}영업일 지연).`, `KOSPI data is as of ${dataAsOf} (${lag} business days behind).`),
              tx('장 마감 전이라 오늘 시세는 쓰지 않고 직전 거래일 종가를 씁니다.', 'The market has not closed yet, so the previous trading day\'s close is used.'),
            ]
          : [],
      data_as_of: dataAsOf,
      expected_date: expected,
      lag_business_days: lag,
      price_status: lag > 0 ? '미수집' : null,
      price_status_detail: lag > 0 ? '장 마감 뒤 시세 수집 기록 없음' : null,
      sources: { kospi: { date: dataAsOf, lag_business_days: lag }, kosdaq: { date: dataAsOf, lag_business_days: lag }, usd_krw: { date: dataAsOf, lag_business_days: lag } },
      news_collected_at: generatedAt,
      disclosure_collected_at: generatedAt,
      last_refresh: { ok: true, finished_at: generatedAt, failed_steps: [] },
      intraday: false,
    },
    summary: {
      text: summaryText,
      source: useLlm ? 'llm' : 'code',
      note: useLlm
        ? tx('AI가 위 계산 결과만으로 쓴 요약입니다(숫자 검증 통과).', 'A summary written by AI from the calculations above only (numbers verified).')
        : tx('코드가 계산 결과로 만든 요약입니다.', 'A summary generated by code from the calculation results.'),
    },
    sections: {
      market: {
        title: tx('오늘의 시장 요약', 'Market summary'),
        status: 'ok',
        indices,
        news: sampleNews(tx('시장', 'market'), 2, tx, dataAsOf ?? localDate),
        news_label: tx('시장 관련 기사(제목에 시장 키워드가 있는 기사, 제목만 보이며 해석하지 않습니다)', 'Market-related articles (titles with market keywords; titles only, no interpretation)'),
        news_empty_text: null,
      },
      holdings: {
        title: tx('내 보유 종목 주요 변화', 'Key changes in my holdings'),
        status: holdingsStatus,
        message: holdingsStatus === 'empty' ? tx('보유 종목이 없습니다. 모의투자 주문 후 확인할 수 있습니다.', 'You have no holdings. Place a paper trade to see this.') : null,
        items: holdingItems,
      },
      watchlist: {
        title: tx('관심 종목 업데이트', 'Watchlist updates'),
        status: watchStatus,
        message: watchStatus === 'empty' ? tx('표시할 관심 종목이 없습니다.', 'There are no watchlist items to show.') : null,
        items: watchItems,
        notable_count: watchItems.filter((w) => w.notable).length,
        dedup_text: overlap > 0 ? tx(`보유 종목과 겹치는 관심 종목 ${overlap}개는 보유 종목 영역에만 표시했습니다.`, `${overlap} watchlist item(s) that overlap with holdings are shown in the holdings section only.`) : null,
      },
      risk: {
        title: tx('포트폴리오 위험 점검', 'Portfolio risk check'),
        status: riskStatus,
        message: riskStatus === 'empty' ? tx('보유 종목이 없어 위험 점검을 하지 않았습니다.', 'There are no holdings, so no risk check was done.') : null,
        texts:
          riskStatus === 'ok'
            ? [
                tx(
                  `상위 1종목 비중 ${top1}% · 상위 3종목 비중 ${top3}% · 현금 비중 ${cashWeight}% · 보유 종목 ${holdingItems.length}개`,
                  `Top holding ${top1}% · top 3 ${top3}% · cash ${cashWeight}% · ${holdingItems.length} holdings`,
                ),
                ...(riskFlags.length === 0 ? [tx('규칙 기준으로 확인된 위험 항목은 없습니다.', 'No risk items were found by the rules.')] : []),
              ]
            : [],
        metrics:
          riskStatus === 'ok'
            ? {
                total_asset: Math.round(totalAsset),
                holding_count: holdingItems.length,
                priced_holding_count: holdingItems.length,
                cash_weight_pct: cashWeight,
                top1_weight_pct: top1,
                top3_weight_pct: top3,
                herfindahl_index: herfindahl,
                market_weights_pct: marketWeights,
              }
            : null,
        flags: riskFlags,
        sector: { status: 'unavailable', text: tx('업종 분류 데이터가 없어 업종 집중도는 계산하지 않았습니다.', 'Sector concentration is not calculated because there is no sector classification data.') },
        etf_exposure: riskStatus === 'ok' ? etfExposure : null,
        cautions,
      },
      checklist: {
        title: tx('오늘 확인할 사항', 'Things to check today'),
        status: checklistStatus,
        message: checklistStatus === 'empty' ? tx('오늘 따로 확인할 항목이 없습니다.', 'There is nothing in particular to check today.') : null,
        items: checklistItems,
      },
    },
    highlights: holdingItems
      .filter((i) => i.day_change_pct != null && Math.abs(i.day_change_pct) >= 3)
      .slice(0, 5)
      .map((i) => ({
        kind: 'day_move',
        text: i.risk_factors[0] ?? `${i.name} ${pct(i.day_change_pct)}`,
        value: i.day_change_pct,
        threshold: 3,
        ticker: i.ticker,
        company_name: i.name,
      })),
    not_advice: tx('확인된 데이터를 정리한 것이며 매수·매도 권유나 가격 예측이 아닙니다.', 'This summarizes verified data and is not a recommendation to buy or sell or a price forecast.'),
    disclaimer: translate(locale, 'system.disclaimer'),
  }
}

function collectSources(content: BriefingContent): BriefingSource[] {
  const out: BriefingSource[] = []
  for (const n of content.sections.market?.news ?? []) out.push({ kind: 'market_news', title: n.title, ticker: null, url: n.url, date: n.published_at })
  for (const h of content.sections.holdings?.items ?? []) {
    for (const n of h.news) out.push({ kind: 'news', title: n.title, ticker: h.ticker, url: n.url, date: n.published_at })
    for (const n of h.indirect_news?.items ?? []) out.push({ kind: 'indirect_news', title: n.title, ticker: h.ticker, url: n.url, date: n.published_at })
    for (const d of h.disclosures) out.push({ kind: 'disclosure', title: d.title, ticker: h.ticker, url: d.source_url, date: d.disclosed_at })
  }
  return out
}

function localDateOf(timezone: string): string {
  return localParts(mockNow().toISOString(), timezone).date
}

async function generateDailyBriefing(input: GenerateBriefingInput = {}): Promise<DailyBriefing> {
  await delay(input.use_llm === false ? 400 : 1800)
  const userId = currentUserId()
  const store = storeFor(userId)
  const state = stateFor(userId)
  const timezone = input.timezone ?? store.preferences.timezone ?? DEFAULT_TIMEZONE
  if (!isValidTimezone(timezone)) throw new ApiError(`알 수 없는 시간대입니다: ${timezone}`, 422)
  const locale = toLocale(input.locale ?? store.preferences.locale)
  const localDate = localDateOf(timezone)

  const sameDay = state.items.filter((b) => b.local_date === localDate && b.timezone === timezone)
  const latest = sameDay.sort((a, b) => b.version - a.version)[0]
  if (latest && !input.regenerate) return { ...latest, created: false }
  if (sameDay.length >= MAX_VERSIONS_PER_DAY) {
    throw new ApiError(`오늘은 브리핑을 ${MAX_VERSIONS_PER_DAY}번까지 만들 수 있습니다. 저장된 브리핑을 확인해 주세요.`, 409)
  }

  const useLlm = input.use_llm !== false
  const content = buildContent(locale, localDate, timezone, userId, useLlm)
  const briefing: DailyBriefing = {
    id: state.nextId++,
    version: sameDay.length + 1,
    local_date: localDate,
    timezone,
    locale,
    generated_at: content.generated_at,
    data_as_of: content.data_as_of,
    market_data_status: content.market_data_status,
    summary_source: content.summary.source,
    section_status: content.section_status,
    tickers: [
      ...[...store.holdings.keys()].flatMap((t) => {
        const m = findCompanyMeta(t)
        return m ? [{ ticker: m.ticker, name: m.name, scope: 'holding' as const }] : []
      }),
      ...store.watchlist
        .filter((w) => !store.holdings.has(w.ticker))
        .flatMap((w) => {
          const m = findCompanyMeta(w.ticker)
          return m ? [{ ticker: m.ticker, name: m.name, scope: 'watchlist' as const }] : []
        }),
    ],
    sources: collectSources(content),
    content,
    // ko-KR이 아닌 브리핑에는 같은 계산 결과의 한국어 원문이 함께 온다("한국어 원문 보기")
    original_content: locale === 'ko-KR' ? null : buildContent('ko-KR', localDate, timezone, userId, useLlm),
    date_group: 'today',
    created: true,
  }
  state.items.push(briefing)
  return briefing
}

function withGroup(b: DailyBriefing): DailyBriefing {
  return { ...b, date_group: dateGroupOf(b.local_date, b.timezone, mockNow()) }
}

async function getCurrentDailyBriefing(timezone?: string): Promise<DailyBriefing | null> {
  await delay(200)
  const userId = currentUserId()
  const store = storeFor(userId)
  const tz = timezone ?? store.preferences.timezone ?? DEFAULT_TIMEZONE
  if (!isValidTimezone(tz)) throw new ApiError(`알 수 없는 시간대입니다: ${tz}`, 422)
  const today = localDateOf(tz)
  const latest = stateFor(userId)
    .items.filter((b) => b.local_date === today && b.timezone === tz)
    .sort((a, b) => b.version - a.version)[0]
  return latest ? { ...withGroup(latest), created: null } : null
}

async function listDailyBriefings(params: BriefingHistoryParams = {}): Promise<DailyBriefingHistory> {
  await delay(200)
  const userId = currentUserId()
  const store = storeFor(userId)
  const timezone = params.timezone ?? store.preferences.timezone ?? DEFAULT_TIMEZONE
  if (!isValidTimezone(timezone)) throw new ApiError(`알 수 없는 시간대입니다: ${timezone}`, 422)
  const page = Math.max(1, params.page ?? 1)
  const size = Math.min(100, Math.max(1, params.size ?? 20))
  const latestOnly = params.latest_only ?? true
  if (params.date_from && params.date_to && params.date_from > params.date_to) {
    throw new ApiError('date_from은 date_to보다 늦을 수 없습니다.', 422)
  }

  const all = stateFor(userId).items.filter(
    (b) => (!params.date_from || b.local_date >= params.date_from) && (!params.date_to || b.local_date <= params.date_to),
  )
  const versionsOn = new Map<string, number>()
  for (const b of all) versionsOn.set(b.local_date, (versionsOn.get(b.local_date) ?? 0) + 1)
  const rows = all
    .filter((b) => !latestOnly || b.version === Math.max(...all.filter((x) => x.local_date === b.local_date).map((x) => x.version)))
    .sort((a, b) => b.local_date.localeCompare(a.local_date) || b.version - a.version)
  const items = rows.slice((page - 1) * size, page * size).map((b) => {
    const local = localParts(b.generated_at, timezone)
    return {
      id: b.id,
      version: b.version,
      versions_on_date: versionsOn.get(b.local_date) ?? 1,
      local_date: b.local_date,
      local_time: local.time,
      date_group: dateGroupOf(b.local_date, timezone, mockNow()),
      generated_at: b.generated_at,
      locale: b.locale,
      data_as_of: b.data_as_of,
      market_data_status: b.market_data_status,
      summary_source: b.summary_source,
      title: b.content.title,
      headline: b.content.freshness.headline,
      summary: b.content.summary.text,
      section_status: b.section_status,
      ticker_count: b.tickers.length,
      detail_path: `/api/briefings/daily/${b.id}`,
    }
  })
  return {
    items,
    page,
    size,
    total: rows.length,
    has_next: page * size < rows.length,
    total_pages: Math.max(1, Math.ceil(rows.length / size)),
    timezone,
    latest_only: latestOnly,
    date_from: params.date_from ?? null,
    date_to: params.date_to ?? null,
  }
}

async function getDailyBriefing(briefingId: number): Promise<DailyBriefing> {
  await delay(150)
  const found = stateFor(currentUserId()).items.find((b) => b.id === briefingId)
  // 남의 브리핑과 없는 id는 구분하지 않는다(둘 다 404)
  if (!found) throw new ApiError(`briefing_id=${briefingId} 브리핑을 찾을 수 없습니다.`, 404)
  return { ...withGroup(found), created: null }
}

export const briefingMock: BriefingApi = {
  generateDailyBriefing,
  getCurrentDailyBriefing,
  listDailyBriefings,
  getDailyBriefing,
}
