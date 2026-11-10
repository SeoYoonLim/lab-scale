import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { api } from '../api'
import { queryKeys } from '../api/queryKeys'
import { useLocale } from '../i18n/useLocale'
import type { EventReaction, EventReactionSummary, EventReturn, EventTypeFilter } from '../types'
import { formatDate, formatPct, trendClass } from '../utils/format'
import ErrorNotice from './ui/ErrorNotice'

const EVENT_TYPE_OPTIONS: { value: EventTypeFilter; label: { key: string; fallback: string } }[] = [
  { value: 'all', label: { key: 'filter.all', fallback: '전체' } },
  { value: 'disclosure', label: { key: 'stock_feed.disclosures_title', fallback: '공시' } },
  { value: 'news', label: { key: 'stock_feed.news_title', fallback: '뉴스' } },
]

const HORIZONS = [
  { key: 'd1', days: 1 },
  { key: 'd5', days: 5 },
  { key: 'd20', days: 20 },
] as const

function signed(value: number) {
  return `${value > 0 ? '+' : ''}${value.toFixed(2)}%p`
}

function ReturnCell({ label, value }: { label: string; value: EventReturn }) {
  const { t } = useLocale()
  return (
    <div className="reaction-cell">
      <span className="reaction-cell-label">{label}</span>
      {value.pct != null ? (
        <>
          <span className={`num reaction-cell-value ${trendClass(value.pct)}`}>{formatPct(value.pct)}</span>
          {value.excess_pct != null && (
            <span
              className="muted small num"
              title={t('reactions.excess_title', { fallback: '같은 기간 시장 지수 대비 초과수익률' })}
            >
              {t('reactions.vs_index', { fallback: '지수 대비 {value}', vars: { value: signed(value.excess_pct) } })}
            </span>
          )}
        </>
      ) : (
        <span className="muted small">{value.reason ?? '-'}</span>
      )}
    </div>
  )
}

function EventRow({ event }: { event: EventReaction }) {
  const { t } = useLocale()
  const when = event.event_at ?? event.event_date
  return (
    <li className="reaction-row">
      <div className="reaction-head">
        <span className={`reaction-type reaction-type-${event.event_type}`}>
          {event.event_type === 'disclosure'
            ? t('stock_feed.disclosures_title', { fallback: '공시' })
            : t('stock_feed.news_title', { fallback: '뉴스' })}
        </span>
        {event.url ? (
          <a href={event.url} target="_blank" rel="noreferrer" className="reaction-title">
            {event.title}
          </a>
        ) : (
          <span className="reaction-title">{event.title}</span>
        )}
      </div>
      <div className="meta muted small">
        <span className="num">{formatDate(when)}</span>
        {!event.time_known && (
          <span>{t('reactions.time_unknown', { fallback: '발표 시각을 몰라 접수일 기준으로 계산했어요' })}</span>
        )}
        {event.volume_ratio != null && (
          <span
            className="num"
            title={t('reactions.volume_title', {
              fallback: '반응 시작일 거래량 ÷ 직전 {n}거래일 평균',
              vars: { n: event.volume_sample_days },
            })}
          >
            {t('reactions.volume_ratio', { fallback: '거래량 평균의 {n}배', vars: { n: event.volume_ratio.toFixed(2) } })}
          </span>
        )}
        {event.other_events_in_window > 0 && (
          <span>
            {t('reactions.other_events', { fallback: '같은 구간에 다른 사건 {n}건', vars: { n: event.other_events_in_window } })}
          </span>
        )}
      </div>
      <div className="reaction-cells">
        {HORIZONS.map((h) => (
          <ReturnCell
            key={h.key}
            label={t('reactions.horizon', { fallback: '{n}일', vars: { n: h.days } })}
            value={event.returns[h.key]}
          />
        ))}
      </div>
      {event.note && <p className="muted small">{event.note}</p>}
    </li>
  )
}

function SummaryTable({ summary }: { summary: EventReactionSummary }) {
  const { t } = useLocale()
  const rows = [
    { days: 1, n: summary.with_d1, avg: summary.avg_d1_pct, up: summary.positive_d1_count, excess: summary.avg_excess_d1_pct },
    { days: 5, n: summary.with_d5, avg: summary.avg_d5_pct, up: summary.positive_d5_count, excess: summary.avg_excess_d5_pct },
    { days: 20, n: summary.with_d20, avg: summary.avg_d20_pct, up: summary.positive_d20_count, excess: summary.avg_excess_d20_pct },
  ]
  // 막대 길이는 세 기간 평균 중 절댓값이 가장 큰 것을 100%로 맞춘 상대 크기다(절대 기준 아님).
  const maxAbs = Math.max(0.5, ...rows.map((r) => Math.abs(r.avg ?? 0)))
  return (
    <table className="reaction-summary">
      <caption className="muted small">
        {t('reactions.summary_caption', {
          fallback: '사건 {n}건의 평균 (수익률이 계산된 사건만 포함해요. 표본 수를 함께 봐주세요)',
          vars: { n: summary.event_count },
        })}
      </caption>
      <thead>
        <tr>
          <th scope="col">{t('reactions.col_period', { fallback: '기간' })}</th>
          <th scope="col">{t('reactions.col_avg', { fallback: '평균 수익률' })}</th>
          <th scope="col">{t('reactions.col_excess', { fallback: '지수 대비' })}</th>
          <th scope="col">{t('reactions.col_up', { fallback: '상승한 사건' })}</th>
          <th scope="col">{t('reactions.col_sample', { fallback: '표본' })}</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => (
          <tr key={r.days}>
            <th scope="row">{t('reactions.horizon', { fallback: '{n}일', vars: { n: r.days } })}</th>
            <td className={`num ${trendClass(r.avg)}`}>
              <span className="avg-cell">
                <span className="bar-track" aria-hidden="true">
                  {r.avg != null && (
                    <span
                      className={`bar bar-${trendClass(r.avg)}`}
                      style={{ width: `${(Math.abs(r.avg) / maxAbs) * 100}%` }}
                    />
                  )}
                </span>
                {formatPct(r.avg)}
              </span>
            </td>
            <td className={`num ${trendClass(r.excess)}`}>{r.excess == null ? '-' : signed(r.excess)}</td>
            <td className="num">{r.n > 0 ? `${r.up} / ${r.n}` : '-'}</td>
            <td className="num">
              {r.n}
              {t('common.unit_count')}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

// GET /api/stocks/{ticker}/event-reactions. 서버가 코드로 계산한 값이고 인과관계가 아니라서, 응답의
// disclaimer를 항상 같이 보여준다(backend/API.md). 일 단위 값이라 종목 시세와 달리 폴링하지 않는다.
export default function EventReactionsCard({ ticker }: { ticker: string }) {
  const { t } = useLocale()
  const [eventType, setEventType] = useState<EventTypeFilter>('all')
  const { data, error, isPending, isPlaceholderData, refetch } = useQuery({
    queryKey: queryKeys.eventReactions(ticker, eventType),
    queryFn: () => api.getEventReactions(ticker, { eventType }),
    placeholderData: keepPreviousData,
    retry: false,
  })

  return (
    <section className="card reaction-card" aria-labelledby="reaction-heading">
      <div className="card-head">
        <h2 id="reaction-heading">{t('reactions.title', { fallback: '뉴스·공시 이후 주가는 어떻게 움직였나' })}</h2>
        <div className="segmented" role="group" aria-label={t('reactions.type_aria', { fallback: '사건 유형' })}>
          {EVENT_TYPE_OPTIONS.map((o) => (
            <button
              key={o.value}
              type="button"
              className={eventType === o.value ? 'seg active' : 'seg'}
              aria-pressed={eventType === o.value}
              onClick={() => setEventType(o.value)}
            >
              {t(o.label.key, { fallback: o.label.fallback })}
            </button>
          ))}
        </div>
      </div>

      {isPending && <p className="muted">{t('common.loading')}</p>}
      {error && <ErrorNotice error={error} onRetry={() => void refetch()} />}

      {data && (
        <div className={isPlaceholderData ? 'is-stale' : undefined} aria-busy={isPlaceholderData}>
          {data.events.length === 0 ? (
            <p className="muted">{t('reactions.empty', { fallback: '최근 기간에 계산할 수 있는 사건이 없어요.' })}</p>
          ) : (
            <>
              <SummaryTable summary={data.summary} />
              <ul className="reaction-list">
                {data.events.map((e) => (
                  <EventRow key={`${e.event_type}-${e.title}-${e.event_at ?? e.event_date}`} event={e} />
                ))}
              </ul>
            </>
          )}
          <p className="notice reaction-disclaimer">{data.disclaimer}</p>
        </div>
      )}
    </section>
  )
}
