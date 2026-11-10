import type {
  CompanyExposure,
  Constituent,
  EtfContribution,
  ExposureDataWarning,
  ExposureFlag,
  LookThroughSnapshot,
  LookThroughSummary,
  LookThroughView,
  SectorExposure,
  UnpricedHolding,
  ViaEtf,
} from './contracts/exposure'
import type { Locale } from './contracts/common'
import { COMPANY_META, findCompanyMeta } from './mock-core'
import { translate } from '../i18n'

// ETF 실질 노출(look-through) 계산 mock (docs/FRONTEND_HANDOFF_ETF_EXPOSURE.md 2절의 공식을 그대로 따른다).
// 시세와 구성종목 데이터만 다르고(진짜 DB가 아니라 아래 표), 계산 규칙은 백엔드와 같다:
//   - 비중의 분모는 총자산(현금 + 직접 주식 + ETF), ETF 평가액은 총자산에 한 번만 들어간다
//   - ETF 간접 노출 = ETF 평가액 x 구성 비중 / 100 (양수 비중만, 같은 기업을 담은 ETF가 여럿이면 합산)
//   - 레버리지·인버스·구성 데이터 없는 ETF는 분해하지 않고 평가액 전체가 "미확인 노출"
//   - 미확인 노출 = 중첩 ETF + 양수 잔여 + 분해하지 않은 ETF

interface MockConstituent {
  name: string
  code: string | null
  weightPct: number
  // 우리 종목 목록(COMPANY_META)에 있으면 그 ticker
  linkedTicker: string | null
  kind: 'company' | 'nested_etf'
}

interface MockEtfDefinition {
  // 구성 기준일이 시세보다 며칠(거래일) 뒤처졌는지. 2 이상이면 "오래된 구성"(stale)이다.
  holdingsLagDays: number
  leverageKind: string | null
  constituentCount: number
  constituents: MockConstituent[]
}

function linked(name: string, ticker: string, weightPct: number): MockConstituent {
  return { name, code: ticker, weightPct, linkedTicker: ticker, kind: 'company' }
}

function unlinked(name: string, code: string | null, weightPct: number): MockConstituent {
  return { name, code, weightPct, linkedTicker: null, kind: 'company' }
}

const KODEX200_UNLINKED: [string, string, number][] = [
  ['KB금융', '105560', 6.2],
  ['삼성바이오로직스', '207940', 5.8],
  ['POSCO홀딩스', '005490', 5.5],
  ['기아', '000270', 5.4],
  ['셀트리온', '068270', 5.2],
  ['신한지주', '055550', 5.1],
  ['현대모비스', '012330', 5.0],
  ['삼성물산', '028260', 4.9],
  ['HD현대중공업', '329180', 4.7],
  ['하나금융지주', '086790', 4.4],
]

// 실제 DB가 아니라 화면 확인용 표다. 구성 비중 합계는 일부러 100%에 딱 맞추지 않았다(99.8 / 99.3) - 운용사가 소수 둘째
// 자리로 반올림해 공시해서 100에서 조금 벗어나는 것이 정상이다(backend/API.md ETF 구성종목).
const MOCK_ETFS: Record<string, MockEtfDefinition> = {
  '069500': {
    holdingsLagDays: 1,
    leverageKind: null,
    constituentCount: 17,
    constituents: [
      linked('삼성전자', '005930', 31.5),
      linked('SK하이닉스', '000660', 9.2),
      linked('NAVER', '035420', 2.1),
      linked('현대차', '005380', 1.8),
      linked('LG에너지솔루션', '373220', 1.5),
      linked('카카오', '035720', 0.9),
      linked('에코프로비엠', '247540', 0.6),
      ...KODEX200_UNLINKED.map(([n, c, w]) => unlinked(n, c, w)),
    ],
  },
  '360750': {
    holdingsLagDays: 5,
    leverageKind: null,
    constituentCount: 504,
    constituents: [
      unlinked('Apple Inc', 'AAPL US EQUITY', 6.9),
      unlinked('Microsoft Corp', 'MSFT US EQUITY', 6.5),
      unlinked('NVIDIA Corp', 'NVDA US EQUITY', 6.4),
      unlinked('Amazon.com Inc', 'AMZN US EQUITY', 3.5),
      unlinked('Meta Platforms Inc', 'META US EQUITY', 2.5),
      unlinked('Alphabet Inc A', 'GOOGL US EQUITY', 2.1),
      unlinked('Alphabet Inc C', 'GOOG US EQUITY', 1.7),
      unlinked('Berkshire Hathaway B', 'BRK/B US EQUITY', 1.6),
      unlinked('Tesla Inc', 'TSLA US EQUITY', 1.5),
      unlinked('Broadcom Inc', 'AVGO US EQUITY', 1.4),
      unlinked('기타 구성종목', null, 65.2),
    ],
  },
  '122630': {
    holdingsLagDays: 1,
    leverageKind: '레버리지',
    constituentCount: 2,
    constituents: [
      linked('삼성전자', '005930', 30),
      { name: 'KODEX 200', code: '069500', weightPct: 22.69, linkedTicker: '069500', kind: 'nested_etf' },
    ],
  },
  '114800': {
    holdingsLagDays: 1,
    leverageKind: '인버스',
    constituentCount: 1,
    constituents: [{ name: 'KODEX 200선물', code: null, weightPct: 100, linkedTicker: null, kind: 'company' }],
  },
}

export interface PricedDate {
  price: number
  date: string
}

export interface LookThroughInput {
  cash: number
  holdings: { ticker: string; quantity: number }[]
  // 종가(없으면 null = 종가 없음). 성과·노출 mock은 "수집된" 시세를, 진단은 진단이 쓴 현재가를 넘긴다.
  priceOf: (ticker: string) => PricedDate | null
  limit: number
  locale: Locale
  // 기대 거래일, 종가 지연 영업일
  expectedDate: string
  lagBusinessDays: number
  // 구성 기준일을 계산할 때 쓰는 "수집된 시세 날짜 목록"(오름차순)
  tradingDates: string[]
}

type LocalizedText = (ko: string, en: string) => string

function textFor(locale: Locale): LocalizedText {
  return (ko, en) => (locale === 'ko-KR' ? ko : en)
}

const round2 = (n: number) => Math.round(n * 100) / 100

function weightOf(amount: number, totalAsset: number): number {
  return totalAsset > 0 ? round2((amount / totalAsset) * 100) : 0
}

export function computeLookThrough(input: LookThroughInput): LookThroughView {
  const { cash, holdings, priceOf, limit, locale, expectedDate, lagBusinessDays, tradingDates } = input
  const tx = textFor(locale)
  const disclaimer = tx(
    '계산 기준: 직접 보유와 ETF 구성종목 비중으로 나눈 기업별 노출입니다. 구성 비중은 운용사 공시 기준이며 현재 구성과 다를 수 있습니다. ' +
      translate('ko-KR', 'system.disclaimer'),
    'Basis: exposure per company from direct holdings and ETF constituent weights. Constituent weights are as published by the asset manager and may differ from the current composition. ' +
      translate('en-US', 'system.disclaimer'),
  )
  const stale = lagBusinessDays >= 2
  const staleNotice = stale
    ? tx(
        `평가 기준 종가는 ${tradingDates.at(-1)}이며 기대 거래일(${expectedDate})보다 ${lagBusinessDays}영업일 지연됐습니다.`,
        `The closing prices used are from ${tradingDates.at(-1)}, ${lagBusinessDays} business days behind the expected trading day (${expectedDate}).`,
      )
    : null

  // 보유를 평가한다(종가 없으면 unpriced - 0원이 아니다).
  const priced: { ticker: string; name: string; assetType: 'stock' | 'etf'; amount: number; date: string }[] = []
  const unpriced: UnpricedHolding[] = []
  for (const h of holdings) {
    const meta = findCompanyMeta(h.ticker)
    const quote = priceOf(h.ticker)
    if (!meta) continue
    if (!quote) {
      unpriced.push({ ticker: meta.ticker, name: meta.name, asset_type: meta.assetType, quantity: h.quantity })
      continue
    }
    priced.push({ ticker: meta.ticker, name: meta.name, assetType: meta.assetType, amount: quote.price * h.quantity, date: quote.date })
  }

  const stockAmount = priced.filter((p) => p.assetType === 'stock').reduce((s, p) => s + p.amount, 0)
  const etfAmount = priced.filter((p) => p.assetType === 'etf').reduce((s, p) => s + p.amount, 0)
  const holdingsValue = stockAmount + etfAmount
  const totalAsset = cash + holdingsValue

  // 기업별 집계(키: linked면 ticker, 아니면 이름)
  interface Acc {
    name: string
    code: string | null
    ticker: string | null
    linked: boolean
    direct: number
    via: number
    viaEtfs: ViaEtf[]
  }
  const accs = new Map<string, Acc>()
  const accFor = (key: string, init: Omit<Acc, 'direct' | 'via' | 'viaEtfs'>): Acc => {
    let a = accs.get(key)
    if (!a) {
      a = { ...init, direct: 0, via: 0, viaEtfs: [] }
      accs.set(key, a)
    }
    return a
  }

  for (const p of priced.filter((x) => x.assetType === 'stock')) {
    accFor(p.ticker, { name: p.name, code: p.ticker, ticker: p.ticker, linked: true }).direct += p.amount
  }

  const etfRows: EtfContribution[] = []
  const warnings: ExposureDataWarning[] = []
  let viaEtfAmount = 0
  let linkedVia = 0
  let unlinkedVia = 0
  let nestedAmount = 0
  let residualNet = 0
  let positiveResidual = 0
  let overAllocated = 0
  let undecomposed = 0
  let etfHoldingsStale = false

  for (const etf of priced.filter((x) => x.assetType === 'etf')) {
    const def = MOCK_ETFS[etf.ticker]
    const asOfIdx = Math.max(0, tradingDates.length - 1 - (def?.holdingsLagDays ?? 0))
    const holdingsAsOf = def ? (tradingDates[asOfIdx] ?? null) : null
    const holdingsStale = def ? def.holdingsLagDays >= 2 : false
    if (holdingsStale) etfHoldingsStale = true

    const base = {
      ticker: etf.ticker,
      name: etf.name,
      eval_amount: etf.amount,
      asset_weight_pct: weightOf(etf.amount, totalAsset),
      holdings_as_of: holdingsAsOf,
      holdings_lag_business_days: def ? def.holdingsLagDays : null,
      holdings_stale: holdingsStale,
      constituent_count: def?.constituentCount ?? 0,
      linked_count: def ? def.constituents.filter((c) => c.linkedTicker && c.kind === 'company').length : 0,
      unlinked_count: def ? def.constituents.filter((c) => !c.linkedTicker && c.kind === 'company').length : 0,
      nested_etf_count: def ? def.constituents.filter((c) => c.kind === 'nested_etf').length : 0,
      positive_weight_sum_pct: def ? round2(def.constituents.reduce((s, c) => s + Math.max(0, c.weightPct), 0)) : null,
      negative_weight_count: 0,
      negative_weight_sum_pct: 0,
      zero_weight_count: 0,
      null_weight_count: 0,
      over_100_count: 0,
      duplicate_count: 0,
    }

    if (!def) {
      undecomposed += etf.amount
      etfRows.push({
        ...base,
        status: 'not_decomposed',
        reason_code: 'no_holdings_data',
        leverage_kind: null,
        notice: null,
        allocated_amount: null,
        nested_etf_amount: null,
        residual_amount: null,
        constituents: [],
        nested_etfs: [],
      })
      warnings.push({
        kind: 'etf_no_holdings_data',
        ticker: etf.ticker,
        name: etf.name,
        message: tx(`${etf.name}은(는) 구성종목 데이터가 없어 기업별로 나누지 않았습니다.`, `${etf.name} has no constituent data, so it was not broken down by company.`),
      })
      continue
    }

    const shown: Constituent[] = def.constituents.slice(0, 10).map((c) => ({
      name: c.name,
      code: c.code,
      linked_ticker: c.linkedTicker,
      weight_pct: c.weightPct,
      amount: null,
      kind: c.kind,
    }))

    if (def.leverageKind) {
      // 레버리지·인버스: 구성종목이 있어도 기업별로 나누지 않는다(배수를 곱하지 않음). 평가액 전체가 미확인 노출.
      undecomposed += etf.amount
      etfRows.push({
        ...base,
        status: 'not_decomposed',
        reason_code: 'leveraged_inverse',
        leverage_kind: def.leverageKind,
        notice: tx('공시 구성 비중이며 경제적 노출이 아닙니다(배수를 곱하지 않음).', 'These are the published constituent weights, not the economic exposure (no leverage multiple applied).'),
        allocated_amount: null,
        nested_etf_amount: null,
        residual_amount: null,
        constituents: shown,
        nested_etfs: [],
      })
      warnings.push({
        kind: 'etf_leveraged_inverse',
        ticker: etf.ticker,
        name: etf.name,
        message: tx(`${etf.name}은(는) 레버리지·인버스 ETF라 기업별로 나누지 않았습니다.`, `${etf.name} is a leveraged/inverse ETF, so it was not broken down by company.`),
      })
      continue
    }

    let allocated = 0
    let nested = 0
    const allRows: Constituent[] = []
    const nestedList: Constituent[] = []
    for (const c of def.constituents) {
      const amount = round2((etf.amount * c.weightPct) / 100)
      const row: Constituent = {
        name: c.name,
        code: c.code,
        linked_ticker: c.linkedTicker,
        weight_pct: c.weightPct,
        amount,
        kind: c.kind,
      }
      allRows.push(row)
      if (c.kind === 'nested_etf') {
        nested += amount
        nestedList.push(row)
        continue
      }
      allocated += amount
      const key = c.linkedTicker ?? `name:${c.name}`
      const acc = accFor(key, { name: c.name, code: c.code, ticker: c.linkedTicker, linked: c.linkedTicker != null })
      acc.via += amount
      acc.viaEtfs.push({
        ticker: etf.ticker,
        name: etf.name,
        amount,
        constituent_weight_pct: c.weightPct,
        asset_weight_pct: weightOf(amount, totalAsset),
      })
      if (c.linkedTicker) linkedVia += amount
      else unlinkedVia += amount
    }
    // 비중 큰 순 상위 10개(중첩 ETF 행도 kind: nested_etf로 같이 보인다)
    const constituents = [...allRows].sort((x, y) => y.weight_pct - x.weight_pct).slice(0, 10)
    const residual = round2(etf.amount - allocated - nested)
    viaEtfAmount += allocated
    nestedAmount += nested
    residualNet += residual
    if (residual > 0) positiveResidual += residual
    else overAllocated += -residual

    const weightSum = base.positive_weight_sum_pct ?? 0
    if (weightSum < 95) {
      warnings.push({
        kind: 'etf_weight_under_100',
        ticker: etf.ticker,
        name: etf.name,
        message: tx(`${etf.name}의 구성 비중 합계가 ${weightSum}%라 나머지는 미확인 노출로 둡니다.`, `${etf.name}'s constituent weights add up to ${weightSum}%, so the rest is counted as unidentified exposure.`),
      })
    }
    if (nestedList.length > 0) {
      warnings.push({
        kind: 'etf_nested_etf',
        ticker: etf.ticker,
        name: etf.name,
        message: tx(`${etf.name}은(는) 다른 ETF를 담고 있어 그 몫은 분해하지 않았습니다.`, `${etf.name} holds another ETF, which was not broken down.`),
      })
    }
    if (holdingsStale) {
      warnings.push({
        kind: 'etf_holdings_stale',
        ticker: etf.ticker,
        name: etf.name,
        date: holdingsAsOf,
        message: tx(`${etf.name}의 구성종목 기준일은 ${holdingsAsOf}입니다. 현재 구성과 다를 수 있습니다.`, `${etf.name}'s constituents are as of ${holdingsAsOf}. The current composition may differ.`),
      })
    }

    etfRows.push({
      ...base,
      status: 'decomposed',
      reason_code: null,
      leverage_kind: null,
      notice: null,
      allocated_amount: round2(allocated),
      nested_etf_amount: round2(nested),
      residual_amount: residual,
      constituents,
      nested_etfs: nestedList,
    })
  }

  const unidentifiedAmount = nestedAmount + positiveResidual + undecomposed

  // 기업 목록(총자산 대비 비중 큰 순)
  const all: CompanyExposure[] = [...accs.values()]
    .map((a) => {
      const total = a.direct + a.via
      const exposureType = a.direct > 0 && a.via > 0 ? 'both' : a.direct > 0 ? 'direct' : 'etf'
      return {
        rank: 0,
        name: a.name,
        code: a.code,
        ticker: a.ticker,
        linked: a.linked,
        sector: null,
        exposure_type: exposureType as CompanyExposure['exposure_type'],
        direct_amount: round2(a.direct),
        via_etf_amount: round2(a.via),
        total_amount: round2(total),
        direct_weight_pct: weightOf(a.direct, totalAsset),
        via_etf_weight_pct: weightOf(a.via, totalAsset),
        total_weight_pct: weightOf(total, totalAsset),
        holdings_weight_pct: holdingsValue > 0 ? round2((total / holdingsValue) * 100) : null,
        via_etfs: a.viaEtfs.sort((x, y) => y.amount - x.amount),
      }
    })
    .sort((x, y) => y.total_amount - x.total_amount)
    .map((c, i) => ({ ...c, rank: i + 1 }))

  const flags: ExposureFlag[] = []
  for (const c of all) {
    if (c.total_weight_pct >= 40) {
      flags.push({
        kind: 'concentration',
        name: c.name,
        code: c.code,
        value_pct: c.total_weight_pct,
        threshold_pct: 40,
        message: tx(
          `${c.name}의 실질 노출이 총자산의 ${c.total_weight_pct}%입니다(기준 40% 이상).`,
          `${c.name}'s look-through exposure is ${c.total_weight_pct}% of total assets (threshold: 40% or more).`,
        ),
      })
    }
    if (c.via_etf_weight_pct >= 10) {
      flags.push({
        kind: 'etf_inflates_weight',
        name: c.name,
        code: c.code,
        value_pct: c.via_etf_weight_pct,
        threshold_pct: 10,
        message: tx(
          `${c.name}의 ETF를 통한 노출이 총자산의 ${c.via_etf_weight_pct}%라 실질 노출(${c.total_weight_pct}%)이 직접 보유(${c.direct_weight_pct}%)보다 큽니다(기준 10%p 이상).`,
          `${c.name}'s exposure via ETFs is ${c.via_etf_weight_pct}% of total assets, so look-through exposure (${c.total_weight_pct}%) is larger than the direct holding (${c.direct_weight_pct}%) (threshold: 10pp or more).`,
        ),
      })
    }
    for (const v of c.via_etfs) {
      if (v.asset_weight_pct >= 10) {
        flags.push({
          kind: 'single_etf_exposure',
          name: c.name,
          code: c.code,
          etf_ticker: v.ticker,
          etf_name: v.name,
          value_pct: v.asset_weight_pct,
          threshold_pct: 10,
          message: tx(
            `${v.name} 한 상품을 통한 ${c.name} 노출이 총자산의 ${v.asset_weight_pct}%입니다(기준 10% 이상).`,
            `Exposure to ${c.name} through ${v.name} alone is ${v.asset_weight_pct}% of total assets (threshold: 10% or more).`,
          ),
        })
      }
    }
  }

  const top1 = all[0] ?? null
  const directTop1 = priced
    .filter((p) => p.assetType === 'stock')
    .reduce((max, p) => Math.max(max, weightOf(p.amount, totalAsset)), 0)

  for (const u of unpriced) {
    warnings.push({
      kind: 'no_price',
      ticker: u.ticker,
      name: u.name,
      message: tx(`${u.name}은(는) 종가가 없어 평가하지 못했습니다.`, `${u.name} could not be valued because there is no closing price.`),
    })
  }
  const newest = priced.reduce((max, p) => (p.date > max ? p.date : max), '')
  for (const p of priced) {
    if (p.date < newest) {
      warnings.push({
        kind: 'older_price',
        ticker: p.ticker,
        name: p.name,
        date: p.date,
        message: tx(`${p.name}의 종가는 ${p.date} 기준으로 다른 종목보다 오래됐습니다.`, `${p.name}'s closing price is from ${p.date}, older than the others.`),
      })
    }
  }

  const sector: SectorExposure = {
    status: 'unavailable',
    covered_weight_pct: totalAsset > 0 ? 0 : null,
    items: [],
    message: tx('업종 분류 데이터가 없어 업종별 집중도는 계산하지 않았습니다.', 'Sector concentration is not calculated because there is no sector classification data.'),
  }

  const summary: LookThroughSummary = {
    cash_balance: cash,
    holdings_value: holdingsValue,
    total_asset: totalAsset,
    stock_amount: stockAmount,
    etf_amount: etfAmount,
    direct_amount: stockAmount,
    via_etf_amount: round2(viaEtfAmount),
    companies_amount: round2(all.reduce((s, c) => s + c.total_amount, 0)),
    linked_via_amount: round2(linkedVia),
    unlinked_via_amount: round2(unlinkedVia),
    nested_etf_amount: round2(nestedAmount),
    residual_amount: round2(residualNet),
    undecomposed_etf_amount: round2(undecomposed),
    unidentified_amount: round2(unidentifiedAmount),
    unidentified_weight_pct: totalAsset > 0 ? weightOf(unidentifiedAmount, totalAsset) : null,
    over_allocated_amount: round2(overAllocated),
    cash_weight_pct: totalAsset > 0 ? weightOf(cash, totalAsset) : null,
    companies_weight_pct: totalAsset > 0 ? weightOf(all.reduce((s, c) => s + c.total_amount, 0), totalAsset) : null,
    holding_count: holdings.length,
    priced_holding_count: priced.length,
    unpriced_count: unpriced.length,
    company_count: all.length,
    direct_top1_weight_pct: priced.some((p) => p.assetType === 'stock') ? directTop1 : null,
    look_through_top1: top1 ? { name: top1.name, code: top1.code, total_weight_pct: top1.total_weight_pct } : null,
  }

  const empty = holdings.length === 0
  const noPrices = !empty && priced.length === 0
  return {
    generated_at: new Date().toISOString(),
    calc_version: 'look_through.v1',
    locale,
    status: empty ? 'empty' : noPrices ? 'no_prices' : 'ok',
    message: empty
      ? tx('보유 종목이 없어 실질 노출을 계산할 수 없습니다. 모의투자 주문 후 확인할 수 있습니다.', 'There are no holdings, so look-through exposure cannot be calculated. Place a paper trade first.')
      : noPrices
        ? tx('보유 종목의 종가가 없어 평가할 수 없습니다.', 'No closing prices are available for your holdings.')
        : null,
    has_account: true,
    valuation_date: priced.length > 0 ? newest : null,
    expected_date: expectedDate,
    price_lag_business_days: lagBusinessDays,
    stale,
    stale_notice: staleNotice,
    etf_holdings_stale: etfHoldingsStale,
    limit,
    summary,
    companies: all.slice(0, limit),
    overlaps: all.filter((c) => c.exposure_type === 'both'),
    etfs: etfRows,
    unpriced,
    sector,
    flags,
    data_warnings: warnings,
    disclaimer,
  }
}

// 진단 저장본에 넣는 스냅샷(진단 당시 구성 기준일·비중을 보존). 기업은 상위 20개까지(백엔드와 같다).
export function toSnapshot(view: LookThroughView, top = 20): LookThroughSnapshot {
  return {
    calc_version: view.calc_version,
    status: view.status,
    valuation_date: view.valuation_date,
    expected_date: view.expected_date,
    price_lag_business_days: view.price_lag_business_days,
    etf_holdings_stale: view.etf_holdings_stale,
    summary: { ...view.summary, holding_top1_weight_pct: view.summary.direct_top1_weight_pct },
    companies: view.companies.slice(0, top),
    overlaps: view.overlaps.slice(0, top),
    etfs: view.etfs,
    unpriced: view.unpriced,
    sector: view.sector,
    flags: view.flags,
    data_warnings: view.data_warnings,
    computed_at: view.generated_at,
    valuation_basis: 'diagnosis_prices',
  }
}

// 구성종목 API용: ETF별 구성 정의를 꺼낸다(mock-etf가 쓴다).
export function mockEtfDefinition(ticker: string): MockEtfDefinition | undefined {
  return MOCK_ETFS[ticker]
}

export function mockEtfTickers(): string[] {
  return COMPANY_META.filter((c) => c.assetType === 'etf').map((c) => c.ticker)
}
