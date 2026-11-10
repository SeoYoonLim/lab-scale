import type { ReactNode } from 'react'
import { dateRangeInvalid } from '../../api/contracts/stockFeed'
import type { FeedFreshness, FeedOrder } from '../../api/contracts/stockFeed'
import { useLocale } from '../../i18n/useLocale'
import { formatDateTime } from '../../utils/format'
import Banner from '../ui/Banner'
import { DEFAULT_FILTERS, filtersActive } from './filters'
import type { FeedFilterState } from './filters'

// 종목 뉴스·공시 카드가 같이 쓰는 필터(기간·정렬)와 수집 지연 띠.
export function FeedFilters({
  value,
  onChange,
  extra,
}: {
  value: FeedFilterState
  onChange: (next: FeedFilterState) => void
  // 카드마다 다른 필터(공시의 정정 여부 등)
  extra?: ReactNode
}) {
  const { t } = useLocale()
  const invalid = dateRangeInvalid(value.dateFrom, value.dateTo)
  return (
    <div className="feed-filters">
      <div className="history-filter-row">
        <label className="inline-select">
          <span className="muted small">{t('stock_feed.date_from', { fallback: '시작일' })}</span>
          <input type="date" value={value.dateFrom} max={value.dateTo || undefined} onChange={(e) => onChange({ ...value, dateFrom: e.target.value })} />
        </label>
        <label className="inline-select">
          <span className="muted small">{t('stock_feed.date_to', { fallback: '종료일' })}</span>
          <input type="date" value={value.dateTo} min={value.dateFrom || undefined} onChange={(e) => onChange({ ...value, dateTo: e.target.value })} />
        </label>
        <label className="inline-select">
          <select
            value={value.order}
            onChange={(e) => onChange({ ...value, order: e.target.value as FeedOrder })}
            aria-label={t('stock_feed.order_newest', { fallback: '최신순' })}
          >
            <option value="desc">{t('stock_feed.order_newest', { fallback: '최신순' })}</option>
            <option value="asc">{t('stock_feed.order_oldest', { fallback: '오래된순' })}</option>
          </select>
        </label>
        {extra}
        {filtersActive(value) && (
          <button type="button" className="link-button" onClick={() => onChange({ ...DEFAULT_FILTERS })}>
            {t('stock_feed.reset', { fallback: '필터 초기화' })}
          </button>
        )}
      </div>
      {invalid && (
        <p className="notice notice-error" role="alert">
          {t('stock_feed.date_invalid', { fallback: '시작일이 종료일보다 늦어요. 날짜를 다시 골라주세요.' })}
        </p>
      )}
    </div>
  )
}

// 전체 수집이 2평일 이상 멈췄을 때(stale) 목록 맨 위에 알린다. 마지막 수집 시각은 뉴스(공시) 전체의 값이다.
// 지연 일수(collection_lag_weekdays)를 아는 경우와 모르는 경우를 문장(키)으로 나눈다 - "N평일째" 같은 한국어 단어를 변수로
// 끼워 넣으면 다른 언어 문장에 한국어가 섞인다.
export function FeedStaleBanner({ freshness, what }: { freshness: FeedFreshness; what: 'news' | 'disclosure' }) {
  const { t, timezone, locale } = useLocale()
  if (!freshness.stale) return null
  const at = formatDateTime(freshness.last_collected_at, timezone, locale)
  const lag = freshness.collection_lag_weekdays
  const text =
    what === 'news'
      ? lag != null
        ? t('stock_feed.news_stale', {
            fallback: '뉴스 수집이 {lag}평일째 멈춰 있어요(마지막 수집 {at}). 최근 기사가 빠져 있을 수 있어요.',
            vars: { lag, at },
          })
        : t('stock_feed.news_stale_unknown', {
            fallback: '뉴스 수집이 멈춰 있어요(마지막 수집 {at}). 최근 기사가 빠져 있을 수 있어요.',
            vars: { at },
          })
      : lag != null
        ? t('stock_feed.disclosure_stale', {
            fallback: '공시 수집이 {lag}평일째 멈춰 있어요(마지막 수집 {at}). 최근 공시가 빠져 있을 수 있어요.',
            vars: { lag, at },
          })
        : t('stock_feed.disclosure_stale_unknown', {
            fallback: '공시 수집이 멈춰 있어요(마지막 수집 {at}). 최근 공시가 빠져 있을 수 있어요.',
            vars: { at },
          })
  return <Banner tone="warn">{text}</Banner>
}
