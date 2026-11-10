import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { api } from '../api'
import { dateRangeInvalid, feedEmptyReason } from '../api/contracts/stockFeed'
import type { CorrectionFilter, DisclosureParams } from '../api/contracts/stockFeed'
import { queryKeys } from '../api/queryKeys'
import { useLocale } from '../i18n/useLocale'
import { formatTradingDate } from '../utils/format'
import Pagination from './Pagination'
import { FeedFilters, FeedStaleBanner } from './stockFeed/FeedControls'
import { DEFAULT_FILTERS } from './stockFeed/filters'
import type { FeedFilterState } from './stockFeed/filters'
import EmptyState from './ui/EmptyState'
import ErrorNotice from './ui/ErrorNotice'
import StatusBadge from './ui/StatusBadge'

const PAGE_SIZE = 10

// 종목 공시 목록(GET /api/stocks/{ticker}/disclosures). 공시는 접수일(날짜)만 알고 시각은 저장하지 않으므로 날짜만 보여준다
// (disclosed_at의 T00:00:00+09:00를 시각으로 쓰지 않는다). 정정 공시는 원본과 별개 공시라 배지만 달고 둘 다 보여준다.
// ETF는 DART 공시 수집 대상이 아니라 200 + supported=false + message다.
export default function StockDisclosuresCard({ ticker }: { ticker: string }) {
  const { t } = useLocale()
  const [offset, setOffset] = useState(0)
  const [filters, setFilters] = useState<FeedFilterState>(DEFAULT_FILTERS)
  const [correction, setCorrection] = useState<CorrectionFilter>('all')

  const invalid = dateRangeInvalid(filters.dateFrom, filters.dateTo)
  const params: DisclosureParams = {
    limit: PAGE_SIZE,
    offset,
    date_from: filters.dateFrom || undefined,
    date_to: filters.dateTo || undefined,
    order: filters.order,
    correction,
  }
  const { data, error, isPending, isPlaceholderData, refetch } = useQuery({
    queryKey: queryKeys.stockDisclosures(ticker, params),
    queryFn: () => api.getStockDisclosures(ticker, params),
    placeholderData: keepPreviousData,
    enabled: !invalid,
    retry: false,
    staleTime: 60_000,
  })

  function changeFilters(next: FeedFilterState) {
    setFilters(next)
    setOffset(0)
  }

  // ETF(supported=false)는 필터가 의미 없으니 안내만 보여준다
  const unsupported = data?.supported === false

  return (
    <section className="card stock-feed-card" aria-labelledby={`disclosures-heading-${ticker}`}>
      <div className="card-head">
        <h2 id={`disclosures-heading-${ticker}`}>{t('stock_feed.disclosures_title', { fallback: '공시' })}</h2>
      </div>

      {!unsupported && (
        <FeedFilters
          value={filters}
          onChange={changeFilters}
          extra={
            <label className="inline-select">
              <select
                value={correction}
                onChange={(e) => {
                  setCorrection(e.target.value as CorrectionFilter)
                  setOffset(0)
                }}
                aria-label={t('stock_feed.correction_all', { fallback: '전체' })}
              >
                <option value="all">{t('stock_feed.correction_all', { fallback: '전체' })}</option>
                <option value="only">{t('stock_feed.correction_only', { fallback: '정정 공시만' })}</option>
                <option value="exclude">{t('stock_feed.correction_exclude', { fallback: '정정 제외' })}</option>
              </select>
            </label>
          }
        />
      )}

      {isPending && !invalid && <p className="muted">{t('common.loading')}</p>}
      {error && <ErrorNotice error={error} onRetry={() => void refetch()} />}

      {data && unsupported && (
        // 서버 message에는 개발자용 API 경로가 들어 있어 그대로 보여주지 않는다
        <EmptyState
          message={t('stock_feed.disclosure_unsupported', {
            fallback: 'ETF는 DART 공시 수집 대상이 아니어서 공시 목록이 없어요. 위의 뉴스를 이용해주세요.',
          })}
        />
      )}

      {data && !unsupported && (
        <div className={isPlaceholderData ? 'is-stale' : undefined} aria-busy={isPlaceholderData}>
          <FeedStaleBanner freshness={data.freshness} what="disclosure" />
          {data.items.length === 0 ? (
            <EmptyState
              message={
                feedEmptyReason(data.freshness) === 'collection_failed'
                  ? t('stock_feed.collection_failed', { fallback: '최근 수집에 실패해서 새 항목이 없을 수 있어요.' })
                  : t('stock_feed.disclosure_empty', { fallback: '이 기간에 저장된 공시가 없어요.' })
              }
            />
          ) : (
            <>
              <ul className="feed-list">
                {data.items.map((item) => (
                  <li key={item.id} className="feed-item">
                    <div className="feed-title-row">
                      {item.source_url ? (
                        <a href={item.source_url} target="_blank" rel="noopener noreferrer" className="feed-title">
                          {item.title}
                        </a>
                      ) : (
                        <span className="feed-title">{item.title}</span>
                      )}
                      {item.is_correction && (
                        <StatusBadge tone="warn">{t('stock_feed.correction_badge', { fallback: '정정' })}</StatusBadge>
                      )}
                    </div>
                    <div className="feed-meta muted small">
                      {/* 접수일은 시간대가 없는 한국 날짜라 Date로 바꾸지 않고 그대로 보여준다 */}
                      <span className="num">{formatTradingDate(item.disclosed_date)}</span>
                      {item.rcept_no && <span className="num">{item.rcept_no}</span>}
                    </div>
                  </li>
                ))}
              </ul>
              <Pagination offset={data.offset} pageSize={data.limit} total={data.total} onChange={setOffset} />
            </>
          )}
          <p className="muted small chart-note">
            {t('stock_feed.disclosure_note', {
              fallback: 'DART 접수일 기준이에요(시각은 저장하지 않아요). 제목을 누르면 DART 원문이 열려요. 정정 공시는 원본과 별개의 공시라 둘 다 보여요.',
            })}
          </p>
        </div>
      )}
    </section>
  )
}
