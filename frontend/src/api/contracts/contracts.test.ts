import { describe, expect, it } from 'vitest'
import { dailyBriefingHistorySchema, dailyBriefingSchema } from './briefing'
import { lookThroughViewSchema } from './exposure'
import { etfHoldingsSchema } from './etf'
import { historySearchSchema } from './history'
import { breadthBarSegments, changeUnavailableKey, marketOverviewSchema } from './market'
import { dateRangeInvalid, feedEmptyReason, otherTickers, stockDisclosuresSchema, stockNewsSchema } from './stockFeed'
import { priceHistoryResponseSchema } from '../schemas'
import { performanceViewSchema } from './performance'
import { preferencesSchema } from './preferences'
import { reportRecordDetailSchema, reportRecordListSchema } from './research'
import briefingFixture from './fixtures/daily-briefing.json'
import etfFixture from './fixtures/etf-holdings.json'
import marketFixture from './fixtures/market-overview.json'
import newsFixture from './fixtures/stock-news.json'
import disclosuresFixture from './fixtures/stock-disclosures.json'
import lookThroughFixture from './fixtures/look-through-ok.json'
import perfHistoryUnavailable from './fixtures/performance-history-unavailable.json'
import perfNoTrades from './fixtures/performance-no-trades.json'
import perfOk from './fixtures/performance-ok.json'

// 이 fixtures는 백엔드 핸드오프 문서(docs/FRONTEND_*.md)와 backend/API.md에 실린 "개발 DB에서 실제로 호출해 받은 응답"을
// 그대로 추출한 것이다(scripts: 문서의 json 코드 블록). 우리가 손으로 쓴 zod 스키마가 진짜 응답 모양을 통과하는지,
// 즉 화면이 502(응답 형식 오류)로 죽지 않는지를 확인한다. 문서가 바뀌면 fixtures를 다시 뽑아 이 테스트로 어긋남을 찾는다.
describe('백엔드 문서의 실제 응답 예시가 스키마를 통과한다', () => {
  it('성과·벤치마크: 정상(ok), 거래 없음(no_trades), 이력 불일치(history_unavailable)', () => {
    const ok = performanceViewSchema.parse(perfOk)
    expect(ok.status).toBe('ok')
    expect(ok.comparison.status).toBe('ok')
    expect(ok.series.points.length).toBeGreaterThan(0)
    expect(ok.metrics.max_drawdown?.pct).toBeLessThan(0)
    expect(ok.warnings.map((w) => w.kind)).toContain('data_delayed')

    const empty = performanceViewSchema.parse(perfNoTrades)
    expect(empty.status).toBe('no_trades')
    // 계산 불가 상태: 수익률은 null이고 그래프 점은 없다(0으로 그리지 않는다)
    expect(empty.summary.period_return_pct).toBeNull()
    expect(empty.series.points).toEqual([])

    const mismatch = performanceViewSchema.parse(perfHistoryUnavailable)
    expect(mismatch.status).toBe('history_unavailable')
    expect(mismatch.warnings[0].kind).toBe('history_mismatch')
  })

  it('ETF 실질 노출(look-through): 기업·겹침·ETF 카드·플래그·데이터 경고', () => {
    const view = lookThroughViewSchema.parse(lookThroughFixture)
    expect(view.status).toBe('ok')
    expect(view.summary.look_through_top1?.name).toBe('삼성전자')
    // 종목 목록에 없는 구성종목(해외 주식)은 linked: false, ticker: null
    expect(view.companies.some((c) => !c.linked && c.ticker === null)).toBe(true)
    // 레버리지 ETF는 분해하지 않고 구성종목 금액이 null이다
    const leveraged = view.etfs.find((e) => e.status === 'not_decomposed')
    expect(leveraged?.reason_code).toBe('leveraged_inverse')
    expect(leveraged?.constituents.every((c) => c.amount === null)).toBe(true)
    expect(view.flags.map((f) => f.kind)).toContain('etf_inflates_weight')
    expect(view.unpriced[0].ticker).toBe('000660')
  })

  it('일일 브리핑: 5개 영역·신선도·요약·근거', () => {
    const b = dailyBriefingSchema.parse(briefingFixture)
    expect(b.market_data_status).toBe('delayed')
    expect(b.content.section_order).toEqual(['market', 'holdings', 'watchlist', 'risk', 'checklist'])
    expect(b.content.freshness.warning).toBe(true)
    expect(b.content.sections.market?.indices.map((i) => i.code)).toEqual(['kospi', 'kosdaq', 'usd_krw'])
    // 직접 뉴스(+09:00)와 간접(업종) 뉴스(+00:00)는 시각 오프셋이 다르다 - 값은 그대로 보존한다
    const holding = b.content.sections.holdings?.items[0]
    expect(holding?.news[0].published_at).toContain('+09:00')
    expect(holding?.indirect_news?.items[0].published_at).toContain('+00:00')
    // 공시는 날짜만 아는 값이라 T00:00:00+09:00
    const disclosure = b.content.sections.watchlist?.items[0].disclosures[0]
    expect(disclosure?.disclosed_at).toBe('2026-11-03T00:00:00+09:00')
    expect(b.content.sections.risk?.etf_exposure?.status).toBe('computed')
    expect(b.content.sections.checklist?.items.length).toBeGreaterThan(0)
    expect(b.original_content).toBeNull()
  })

  it('ETF 구성종목: 비중·기준일·면책', () => {
    const h = etfHoldingsSchema.parse(etfFixture)
    expect(h.items[0]).toMatchObject({ rank: 1, name: '삼성전자', code: '005930' })
    expect(h.holding_count).toBe(200)
    expect(h.stale).toBe(false)
  })
})

describe('문서 예시를 그대로 옮긴 작은 응답들', () => {
  it('통합 기록 검색(리서치·진단 항목, counts, 날짜 그룹)', () => {
    const response = {
      type: 'all',
      keyword: 'SK하이닉스',
      terms: ['SK하이닉스'],
      date_from: null,
      date_to: null,
      timezone: 'Asia/Seoul',
      sort: 'created_at,desc',
      page: 1,
      size: 20,
      total: 2,
      total_pages: 1,
      has_next: false,
      counts: { all: 2, research: 1, diagnosis: 1 },
      items: [
        {
          kind: 'research',
          id: 32500,
          created_at: '2026-11-05T01:00:00+00:00',
          local_date: '2026-11-05',
          local_time: '10:00',
          date_group: 'today',
          title: 'SK하이닉스 전망 알려줘',
          summary: 'SK하이닉스 단기 방향은 상승 쪽입니다.',
          locale: 'ko-KR',
          company_names: ['SK하이닉스'],
          tickers: ['000660'],
          detail_path: '/api/research/32500',
          research: {
            report_id: 32500,
            previous_report_id: null,
            question: 'SK하이닉스 전망 알려줘',
            analysis_type: 'outlook',
            analysis_types: ['outlook', 'news', 'price'],
            source_count: 2,
            used_tools: ['stock_tool', 'news_tool'],
          },
          diagnosis: null,
        },
        {
          kind: 'diagnosis',
          id: 51639,
          created_at: '2026-11-05T00:30:00+00:00',
          local_date: '2026-11-05',
          local_time: '09:30',
          date_group: 'today',
          title: '삼성전자 외 2종목 진단',
          summary: '총자산 9,000,000원 중 현금이 3,000,000원(비중 33.33%)이고, …',
          locale: 'ko-KR',
          company_names: ['삼성전자', 'SK하이닉스', '에코프로비엠'],
          tickers: ['005930', '000660', '247540'],
          detail_path: '/api/portfolio/diagnosis/51639',
          research: null,
          diagnosis: {
            diagnosis_id: 51639,
            source: 'rule_based',
            total_asset: 9000000.0,
            holdings_count: 3,
            cash_weight_pct: 33.33,
            top_holdings: [{ ticker: '005930', company_name: '삼성전자', weight_pct: 22.22 }],
            risk_summary: '규칙 기준으로 확인된 위험 항목은 없습니다.',
            flags: [],
            flag_codes: [],
            generated_at: '2026-11-05T00:56:00.344219+00:00',
          },
        },
      ],
    }
    const parsed = historySearchSchema.parse(response)
    expect(parsed.counts.diagnosis).toBe(1)
    expect(parsed.items[0].research?.analysis_type).toBe('outlook')
    expect(parsed.items[1].research).toBeNull()
  })

  it('계정 설정과 리서치 기록 상세(번역 상태·원문)', () => {
    expect(
      preferencesSchema.parse({
        preferred_locale: 'ko-KR',
        preferred_timezone: null,
        effective_timezone: 'Asia/Seoul',
        supported_locales: ['ko-KR', 'en-US', 'ja-JP', 'zh-CN'],
        default_locale: 'ko-KR',
      }).effective_timezone,
    ).toBe('Asia/Seoul')

    const detail = reportRecordDetailSchema.parse({
      report_id: 32500,
      previous_report_id: null,
      question: 'SK하이닉스 전망 알려줘',
      answer: 'SK하이닉스 단기 방향은 상승 쪽입니다.',
      summary: null,
      company_name: null,
      created_at: '2026-11-05T01:00:00+00:00',
      used_tools: ['stock_tool', 'news_tool'],
      sources: [{ tool: 'news_tool', type: 'news', title: 'HBM 수요 확대', company_names: ['SK하이닉스'], company_filter: null, url: 'https://example.com/0' }],
      disclaimer: '…',
      title: 'SK하이닉스 전망 알려줘',
      company_names: ['SK하이닉스'],
      tickers: ['000660'],
      analysis_type: 'outlook',
      analysis_types: ['outlook'],
      source_count: 2,
      local_date: '2026-11-05',
      local_time: '10:00',
      date_group: 'today',
      timezone: 'Asia/Seoul',
      data_basis: [{ tool: 'stock_tool', company_name: 'SK하이닉스', ticker: '000660', base_date: '2026-11-04' }],
      locale: 'ko-KR',
      translation_status: null,
      original_answer: null,
    })
    expect(detail.data_basis[0].period_start_date).toBeUndefined()

    const list = reportRecordListSchema.parse({ total: 0, has_next: false, total_pages: 0, timezone: 'Asia/Seoul', items: [] })
    expect(list.items).toEqual([])
  })

  it('브리핑 이력 목록(항목 필드·detail_path)', () => {
    const history = dailyBriefingHistorySchema.parse({
      items: [
        {
          id: 47,
          version: 3,
          versions_on_date: 3,
          local_date: '2026-11-06',
          local_time: '14:23',
          date_group: 'today',
          generated_at: '2026-11-06T14:23:31+09:00',
          locale: 'ja-JP',
          data_as_of: '2026-11-03',
          market_data_status: 'delayed',
          summary_source: 'llm',
          title: '2026-11-06 デイリー投資ブリーフィング',
          headline: '株価が2営業日遅れています。',
          summary: '…',
          section_status: { market: 'ok', holdings: 'ok', watchlist: 'ok', risk: 'ok', checklist: 'ok' },
          ticker_count: 4,
          detail_path: '/api/briefings/daily/47',
        },
      ],
      page: 1,
      size: 20,
      total: 1,
      has_next: false,
      total_pages: 1,
      timezone: 'Asia/Seoul',
      latest_only: true,
      date_from: null,
      date_to: null,
    })
    expect(history.items[0].versions_on_date).toBe(3)
  })

  it('시장 요약(market/overview): 정상 응답과 등락이 비는 경우, 데이터 없음', () => {
    const ok = marketOverviewSchema.parse(marketFixture)
    expect(ok.status).toBe('ok')
    expect(ok.freshness.status).toBe('delayed')
    expect(ok.indices.map((i) => i.code)).toEqual(['KS11', 'KQ11'])
    expect(ok.breadth?.universe).toBe('collected_stocks')
    // 막대는 서버가 준 비율을 그대로 쓴다(우리가 다시 계산하지 않는다)
    const bar = breadthBarSegments(ok.breadth!)
    expect(bar?.map((s) => s.key)).toEqual(['up', 'flat', 'down'])
    expect(bar?.map((s) => s.pct)).toEqual([22.3, 1, 76.7])

    // 수집 공백: 등락이 null이고 이유가 온다 - 0으로 바꾸지 않고, 모르는 이유는 이유 없이 "—"만
    const gap = marketOverviewSchema.parse({
      ...marketFixture,
      indices: [{ ...marketFixture.indices[0], change: null, change_pct: null, change_unavailable_reason: 'previous_trading_day_missing' }, marketFixture.indices[1]],
    })
    expect(gap.indices[0].change_pct).toBeNull()
    expect(changeUnavailableKey(gap.indices[0].change_unavailable_reason)?.key).toBe('market.change_gap')
    expect(changeUnavailableKey('something_new')).toBeNull()

    // 데이터가 없어도 200: 지수 값은 null, breadth는 null
    const none = marketOverviewSchema.parse({
      ...marketFixture,
      status: 'no_data',
      as_of: null,
      missing: ['KS11', 'KQ11', 'breadth'],
      freshness: { ...marketFixture.freshness, status: 'no_data', lag_business_days: null },
      indices: marketFixture.indices.map((i) => ({ ...i, date: null, close: null, previous_date: null, change: null, change_pct: null, lag_business_days: null })),
      breadth: null,
    })
    expect(none.status).toBe('no_data')
    expect(none.breadth).toBeNull()
    // 분모가 0이라 비율이 없으면 막대를 그리지 않는다
    expect(breadthBarSegments({ ...ok.breadth!, up_ratio_pct: null })).toBeNull()
  })

  it('종목 뉴스·공시 목록: 정상 응답, 정정 공시, ETF(supported=false), 빈 목록의 이유', () => {
    const news = stockNewsSchema.parse(newsFixture)
    // 문서 예시의 related_tickers에는 현재 종목(005930)이 들어 있지 않다 - 같은 기사가 연결된 종목들일 뿐이다
    expect(news.items[0].related_tickers.length).toBeGreaterThan(0)
    // 같은 기사가 연결된 다른 종목만 링크로 쓴다(현재 종목·중복 제외)
    expect(otherTickers(news.items[0], '005930')).toEqual(['000270', '004170', '005380', '069620'])
    expect(otherTickers({ ...news.items[0], related_tickers: ['005930', '000270', '000270'] }, '005930')).toEqual(['000270'])
    expect(news.freshness.stale).toBe(true)

    const disclosures = stockDisclosuresSchema.parse(disclosuresFixture)
    expect(disclosures.supported).toBe(true)
    expect(disclosures.items[0].is_correction).toBe(true)
    // 공시는 접수일만 알고 시각은 없다: disclosed_at의 T00:00:00+09:00를 시각으로 쓰면 안 된다
    expect(disclosures.items[0].disclosed_date).toBe('2026-10-29')

    // ETF: 200 + supported=false + message + items []
    const etf = stockDisclosuresSchema.parse({ ...disclosuresFixture, asset_type: 'etf', supported: false, message: '공시 수집 대상이 아닙니다.', total: 0, total_pages: 0, items: [] })
    expect(etf.supported).toBe(false)

    // 비었을 때 이유: 마지막 수집이 failed면 "수집 실패", 아니면 "그 기간에 없다"
    expect(feedEmptyReason(news.freshness)).toBe('none')
    expect(feedEmptyReason({ ...news.freshness, last_collection: { ...news.freshness.last_collection!, status: 'failed' } })).toBe('collection_failed')
    expect(feedEmptyReason({ ...news.freshness, last_collection: null })).toBe('none')

    expect(dateRangeInvalid('2026-11-05', '2026-11-01')).toBe(true)
    expect(dateRangeInvalid('2026-11-01', '2026-11-01')).toBe(false)
    expect(dateRangeInvalid('2026-11-01', '')).toBe(false)
  })

  it('주가 이력: 새 필드(available_count·first_date·missing_trading_days)가 있어도 없어도(옛 서버) 통과한다', () => {
    const base = {
      ticker: '005930', company_name: '삼성전자', asset_type: 'stock', days: 365, count: 1, price_as_of: '2026-11-03',
      items: [{ date: '2026-11-03', close: 70000, volume: 1000, change_pct: 0.5, open: 69500, high: 70500, low: 69000 }],
    }
    expect(priceHistoryResponseSchema.parse(base).available_count).toBeUndefined()
    const extended = priceHistoryResponseSchema.parse({ ...base, available_count: 342, first_date: '2025-06-02', missing_trading_days: 2 })
    expect(extended.available_count).toBe(342)
    expect(extended.missing_trading_days).toBe(2)
    expect(priceHistoryResponseSchema.parse({ ...base, available_count: 0, first_date: null }).first_date).toBeNull()
  })
})

describe('스키마가 문서와 다른 모양은 막는다', () => {
  it('성과의 status가 모르는 값이거나 숫자 필드가 문자열이면 거부한다', () => {
    expect(performanceViewSchema.safeParse({ ...perfOk, status: 'maybe' }).success).toBe(false)
    expect(performanceViewSchema.safeParse({ ...perfOk, summary: { ...perfOk.summary, end_value: '9479960' } }).success).toBe(false)
  })

  it('노출의 비중이 문자열이면 거부한다', () => {
    const broken = { ...lookThroughFixture, summary: { ...lookThroughFixture.summary, total_asset: '4500000' } }
    expect(lookThroughViewSchema.safeParse(broken).success).toBe(false)
  })

  it('브리핑은 영역 안의 세부 필드가 빠져도(방어적으로) 통과하고 기본값을 채운다', () => {
    const minimal = {
      ...briefingFixture,
      content: {
        ...briefingFixture.content,
        sections: { market: { title: '시장', status: 'ok' }, holdings: { title: '보유', status: 'empty', message: '없음' } },
      },
    }
    const parsed = dailyBriefingSchema.parse(minimal)
    expect(parsed.content.sections.market?.indices).toEqual([])
    expect(parsed.content.sections.holdings?.items).toEqual([])
    expect(parsed.content.sections.risk).toBeNull()
  })
})
