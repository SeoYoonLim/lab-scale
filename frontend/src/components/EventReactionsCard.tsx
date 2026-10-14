import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { api } from '../api'
import { queryKeys } from '../api/queryKeys'
import type { EventReaction, EventReactionSummary, EventReturn, EventTypeFilter } from '../types'
import { formatDate, formatPct, trendClass } from '../utils/format'

const EVENT_TYPE_OPTIONS: { value: EventTypeFilter; label: string }[] = [
  { value: 'all', label: '전체' },
  { value: 'disclosure', label: '공시' },
  { value: 'news', label: '뉴스' },
]

const HORIZONS = [
  { key: 'd1', label: '1일' },
  { key: 'd5', label: '5일' },
  { key: 'd20', label: '20일' },
] as const

function signed(value: number) {
  return `${value > 0 ? '+' : ''}${value.toFixed(2)}%p`
}

function ReturnCell({ label, value }: { label: string; value: EventReturn }) {
  return (
    <div className="reaction-cell">
      <span className="reaction-cell-label">{label}</span>
      {value.pct != null ? (
        <>
          <span className={`num reaction-cell-value ${trendClass(value.pct)}`}>{formatPct(value.pct)}</span>
          {value.excess_pct != null && (
            <span className="muted small num" title="같은 기간 시장 지수 대비 초과수익률">
              지수 대비 {signed(value.excess_pct)}
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
  const when = event.event_at ?? event.event_date
  return (
    <li className="reaction-row">
      <div className="reaction-head">
        <span className={`reaction-type reaction-type-${event.event_type}`}>
          {event.event_type === 'disclosure' ? '공시' : '뉴스'}
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
        {!event.time_known && <span>발표 시각을 몰라 접수일 기준으로 계산했어요</span>}
        {event.volume_ratio != null && (
          <span className="num" title={`반응 시작일 거래량 ÷ 직전 ${event.volume_sample_days}거래일 평균`}>
            거래량 평균의 {event.volume_ratio.toFixed(2)}배
          </span>
        )}
        {event.other_events_in_window > 0 && <span>같은 구간에 다른 사건 {event.other_events_in_window}건</span>}
      </div>
      <div className="reaction-cells">
        {HORIZONS.map((h) => (
          <ReturnCell key={h.key} label={h.label} value={event.returns[h.key]} />
        ))}
      </div>
      {event.note && <p className="muted small">{event.note}</p>}
    </li>
  )
}

function SummaryTable({ summary }: { summary: EventReactionSummary }) {
  const rows = [
    { label: '1일', n: summary.with_d1, avg: summary.avg_d1_pct, up: summary.positive_d1_count, excess: summary.avg_excess_d1_pct },
    { label: '5일', n: summary.with_d5, avg: summary.avg_d5_pct, up: summary.positive_d5_count, excess: summary.avg_excess_d5_pct },
    { label: '20일', n: summary.with_d20, avg: summary.avg_d20_pct, up: summary.positive_d20_count, excess: summary.avg_excess_d20_pct },
  ]
  // 막대 길이는 세 기간 평균 중 절댓값이 가장 큰 것을 100%로 맞춘 상대 크기다(절대 기준 아님).
  const maxAbs = Math.max(0.5, ...rows.map((r) => Math.abs(r.avg ?? 0)))
  return (
    <table className="reaction-summary">
      <caption className="muted small">
        사건 {summary.event_count}건의 평균 (수익률이 계산된 사건만 포함해요. 표본 수를 함께 봐주세요)
      </caption>
      <thead>
        <tr>
          <th scope="col">기간</th>
          <th scope="col">평균 수익률</th>
          <th scope="col">지수 대비</th>
          <th scope="col">상승한 사건</th>
          <th scope="col">표본</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => (
          <tr key={r.label}>
            <th scope="row">{r.label}</th>
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
            <td className="num">{r.n}건</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

// GET /api/stocks/{ticker}/event-reactions. 서버가 코드로 계산한 값이고 인과관계가 아니라서, 응답의
// disclaimer를 항상 같이 보여준다(backend/API.md). 일 단위 값이라 종목 시세와 달리 폴링하지 않는다.
export default function EventReactionsCard({ ticker }: { ticker: string }) {
  const [eventType, setEventType] = useState<EventTypeFilter>('all')
  const { data, error, isPending, isPlaceholderData } = useQuery({
    queryKey: queryKeys.eventReactions(ticker, eventType),
    queryFn: () => api.getEventReactions(ticker, { eventType }),
    placeholderData: keepPreviousData,
    retry: false,
  })

  return (
    <section className="card reaction-card" aria-labelledby="reaction-heading">
      <div className="card-head">
        <h2 id="reaction-heading">뉴스·공시 이후 주가는 어떻게 움직였나</h2>
        <div className="segmented" role="group" aria-label="사건 유형">
          {EVENT_TYPE_OPTIONS.map((o) => (
            <button
              key={o.value}
              type="button"
              className={eventType === o.value ? 'seg active' : 'seg'}
              aria-pressed={eventType === o.value}
              onClick={() => setEventType(o.value)}
            >
              {o.label}
            </button>
          ))}
        </div>
      </div>

      {isPending && <p className="muted">불러오는 중…</p>}
      {error && <p className="notice notice-error">{error.message}</p>}

      {data && (
        <div className={isPlaceholderData ? 'is-stale' : undefined} aria-busy={isPlaceholderData}>
          {data.events.length === 0 ? (
            <p className="muted">최근 기간에 계산할 수 있는 사건이 없어요.</p>
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
