import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api'
import { dateRangeInvalid, feedEmptyReason, otherTickers } from '../api/contracts/stockFeed'
import type { FeedParams } from '../api/contracts/stockFeed'
import { queryKeys } from '../api/queryKeys'
import { useLocale } from '../i18n/useLocale'
import { formatDate, formatTime } from '../utils/format'
import Pagination from './Pagination'
import { FeedFilters, FeedStaleBanner } from './stockFeed/FeedControls'
import { DEFAULT_FILTERS } from './stockFeed/filters'
import type { FeedFilterState } from './stockFeed/filters'
import EmptyState from './ui/EmptyState'
import ErrorNotice from './ui/ErrorNotice'

const PAGE_SIZE = 10

// 종목 뉴스 목록(GET /api/stocks/{ticker}/news). 저장된 기사를 페이지로 나눠 보여줄 뿐 해석하지 않는다.
// 요약(summary)은 네이버 검색 API가 준 기사 앞부분 발췌라서 "AI 요약"이라고 쓰지 않고, 없으면 숨긴다.
export default function StockNewsCard({ ticker }: { ticker: string }) {
  const { t } = useLocale()
  const [offset, setOffset] = useState(0)
  const [filters, setFilters] = useState<FeedFilterState>(DEFAULT_FILTERS)

  // 날짜 범위가 거꾸로면 서버가 422라서 보내지 않는다(입력 옆 안내만 보인다)
  const invalid = dateRangeInvalid(filters.dateFrom, filters.dateTo)
  const params: FeedParams = {
    limit: PAGE_SIZE,
    offset,
    date_from: filters.dateFrom || undefined,
    date_to: filters.dateTo || undefined,
    order: filters.order,
  }
  const { data, error, isPending, isPlaceholderData, refetch } = useQuery({
    queryKey: queryKeys.stockNews(ticker, params),
    queryFn: () => api.getStockNews(ticker, params),
    placeholderData: keepPreviousData,
    enabled: !invalid,
    retry: false,
    staleTime: 60_000,
  })

  function changeFilters(next: FeedFilterState) {
    setFilters(next)
    setOffset(0)
  }

  return (
    <section className="card stock-feed-card" aria-labelledby={`news-heading-${ticker}`}>
      <div className="card-head">
        <h2 id={`news-heading-${ticker}`}>{t('stock_feed.news_title', { fallback: '뉴스' })}</h2>
      </div>
      <FeedFilters value={filters} onChange={changeFilters} />

      {isPending && !invalid && <p className="muted">{t('common.loading')}</p>}
      {error && <ErrorNotice error={error} onRetry={() => void refetch()} />}

      {data && (
        <div className={isPlaceholderData ? 'is-stale' : undefined} aria-busy={isPlaceholderData}>
          <FeedStaleBanner freshness={data.freshness} what="news" />
          {data.items.length === 0 ? (
            <EmptyState
              message={
                feedEmptyReason(data.freshness) === 'collection_failed'
                  ? t('stock_feed.collection_failed', { fallback: '최근 수집에 실패해서 새 항목이 없을 수 있어요.' })
                  : t('stock_feed.news_empty', { fallback: '이 기간에 저장된 뉴스가 없어요.' })
              }
            />
          ) : (
            <>
              <ul className="feed-list">
                {data.items.map((item) => {
                  const others = otherTickers(item, ticker)
                  return (
                    <li key={item.id} className="feed-item">
                      {item.url ? (
                        <a href={item.url} target="_blank" rel="noopener noreferrer" className="feed-title">
                          {item.title}
                        </a>
                      ) : (
                        <span className="feed-title">{item.title}</span>
                      )}
                      <div className="feed-meta muted small">
                        {item.source && <span>{item.source}</span>}
                        {/* 게시 시각은 KST 오프셋이 붙어 오므로 Date로 읽어 서울 시간으로 보여준다 */}
                        {item.published_at && (
                          <span className="num">
                            {formatDate(item.published_at)} {formatTime(item.published_at)}
                          </span>
                        )}
                      </div>
                      {item.summary && <p className="feed-summary muted small">{item.summary}</p>}
                      {others.length > 0 && (
                        <p className="feed-related small">
                          <span className="muted">{t('stock_feed.also_mentioned', { fallback: '함께 언급된 종목' })}</span>{' '}
                          {others.map((other) => (
                            <Link key={other} to={`/companies/${other}`} className="feed-related-link num">
                              {other}
                            </Link>
                          ))}
                        </p>
                      )}
                    </li>
                  )
                })}
              </ul>
              <Pagination offset={data.offset} pageSize={data.limit} total={data.total} onChange={setOffset} />
            </>
          )}
          <p className="muted small chart-note">
            {t('stock_feed.news_note', {
              fallback: '제목에 종목명이 없는 시황 기사도 섞여 있을 수 있어요. 기사 앞부분은 네이버 검색 결과이고, 해석하지 않은 원문이에요.',
            })}
          </p>
        </div>
      )}
    </section>
  )
}
