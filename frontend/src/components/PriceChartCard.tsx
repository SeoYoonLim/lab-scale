import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { useId, useRef, useState } from 'react'
import type { KeyboardEvent, PointerEvent } from 'react'
import { api } from '../api'
import ErrorNotice from './ui/ErrorNotice'
import { queryKeys } from '../api/queryKeys'
import { useLocale } from '../i18n/useLocale'
import { computeChart, movingAverage, nearestIndex, periodReturn, summarizeHistory } from '../utils/priceChart'
import type { ChartLayout } from '../utils/priceChart'
import { formatDate, formatPct, formatPrice, formatVolume, trendClass } from '../utils/format'

// days는 달력 일이 아니라 "최근 N거래일"이다(backend/API.md). 데이터 시작일 때문에 실제 개수(count)가
// 더 적을 수 있어서, 아래에서 항상 count를 기준으로 그리고 모자라면 그 사실을 알려준다.
const RANGES = [{ days: 20 }, { days: 60 }, { days: 90 }, { days: 180 }, { days: 365 }] as const

// 180·365는 저장된 시세가 그만큼 있어야 의미가 있다(available_count). 모자라면 버튼을 끄고 몇 거래일만 있는지 알려준다.
// 20·60·90은 모자라도 그대로 눌러서 "수집 시작일 때문에 적게 보여드려요" 안내를 보게 한다.
const LONG_RANGE_FROM = 180

// 이동평균선: 화면에서 종가로 계산한 단순이동평균이다(서버가 주는 값이 아니다). 창(window)만큼 거래일이 쌓이기 전 구간은 그리지 않는다.
const MOVING_AVERAGES = [
  { window: 5, color: '#e08a00' },
  { window: 20, color: '#12a05c' },
  { window: 60, color: '#8b5cf6' },
] as const

// 기간별 수익률(N거래일 전 종가 대비). 달력 일이 아니라 거래일이다.
const RETURN_WINDOWS = [{ tradingDays: 5 }, { tradingDays: 20 }, { tradingDays: 60 }] as const

const LAYOUT: ChartLayout = {
  width: 720,
  height: 300,
  volumeHeight: 56,
  paddingLeft: 8,
  paddingRight: 64,
  paddingTop: 12,
  paddingBottom: 22,
}

export default function PriceChartCard({ ticker }: { ticker: string }) {
  const { t } = useLocale()
  const [days, setDays] = useState<number>(60)
  const [visibleMas, setVisibleMas] = useState<number[]>([])
  const [activeIndex, setActiveIndex] = useState<number | null>(null)
  const svgRef = useRef<SVGSVGElement>(null)
  const gradientId = useId()

  // 일별 저장 데이터라 폴링하지 않는다(현재가는 위쪽 카드의 realtime-price가 3초마다 갱신).
  const { data, error, isPending, isPlaceholderData, refetch } = useQuery({
    queryKey: queryKeys.priceHistory(ticker, days),
    queryFn: () => api.getPriceHistory(ticker, days),
    placeholderData: keepPreviousData,
    retry: false,
    staleTime: 60_000,
  })

  const items = data?.items ?? []
  const geometry = computeChart(items, LAYOUT)
  const closes = items.map((i) => i.close)
  const maLines = MOVING_AVERAGES.filter((m) => visibleMas.includes(m.window)).map((m) => {
    const values = movingAverage(closes, m.window)
    let path = ''
    values.forEach((v, i) => {
      const p = geometry.points[i]
      if (v == null || !p) return
      path += `${path === '' ? 'M' : 'L'}${p.x.toFixed(1)} ${geometry.yOf(v).toFixed(1)} `
    })
    return { ...m, path: path.trim(), enough: values.some((v) => v != null) }
  })
  const summary = summarizeHistory(items)
  const tone = trendClass(summary?.changePct ?? 0)
  const lineColor = tone === 'up' ? 'var(--up)' : tone === 'down' ? 'var(--down)' : 'var(--ink-muted)'
  const active = activeIndex != null && activeIndex < items.length ? items[activeIndex] : null
  const activePoint = activeIndex != null ? geometry.points[activeIndex] : undefined

  function indexFromPointer(event: PointerEvent<SVGSVGElement>) {
    const svg = svgRef.current
    if (!svg) return null
    const rect = svg.getBoundingClientRect()
    if (rect.width === 0) return null
    // 화면 좌표 -> viewBox 좌표
    const x = ((event.clientX - rect.left) / rect.width) * LAYOUT.width
    const index = nearestIndex(geometry.points, x)
    return index >= 0 ? index : null
  }

  function handleKeyDown(event: KeyboardEvent<SVGSVGElement>) {
    if (items.length === 0) return
    if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
      event.preventDefault()
      const step = event.key === 'ArrowLeft' ? -1 : 1
      setActiveIndex((prev) => Math.min(items.length - 1, Math.max(0, (prev ?? items.length - 1) + step)))
    } else if (event.key === 'Escape') {
      setActiveIndex(null)
    }
  }

  const shortfall = data != null && data.count > 0 && data.count < data.days
  const available = data?.available_count
  const unavailableRange = (rangeDays: number) =>
    rangeDays >= LONG_RANGE_FROM && available != null && available < rangeDays
  const hasDisabledRange = RANGES.some((r) => unavailableRange(r.days))

  return (
    <section className="card price-chart-card" aria-labelledby="price-chart-heading">
      <div className="card-head">
        <h2 id="price-chart-heading">{t('chart.title', { fallback: '주가 추이' })}</h2>
        <div className="segmented" role="group" aria-label={t('chart.range_aria', { fallback: '조회 기간' })}>
          {RANGES.map((range) => (
            <button
              key={range.days}
              type="button"
              className={days === range.days ? 'seg active' : 'seg'}
              aria-pressed={days === range.days}
              disabled={unavailableRange(range.days)}
              title={
                unavailableRange(range.days)
                  ? t('chart.range_only', { fallback: '{n}거래일만 있어요', vars: { n: available ?? 0 } })
                  : undefined
              }
              onClick={() => {
                setDays(range.days)
                setActiveIndex(null)
              }}
            >
              {t('chart.range_days', { fallback: '{n}거래일', vars: { n: range.days } })}
            </button>
          ))}
        </div>
      </div>

      <div className="ma-toggles" role="group" aria-label={t('chart.ma_aria', { fallback: '이동평균선' })}>
        {MOVING_AVERAGES.map((m) => (
          <label key={m.window} className="check-label">
            <input
              type="checkbox"
              checked={visibleMas.includes(m.window)}
              onChange={(e) =>
                setVisibleMas((prev) => (e.target.checked ? [...prev, m.window] : prev.filter((w) => w !== m.window)))
              }
            />
            <span className="legend-swatch" style={{ background: m.color }} aria-hidden="true" />
            <span>{t('chart.ma_line', { fallback: '{n}일선', vars: { n: m.window } })}</span>
          </label>
        ))}
      </div>

      {hasDisabledRange && (
        <p className="muted small">
          {t('chart.long_range_unavailable', {
            fallback: '이 종목은 저장된 주가가 {n}거래일이라 {min}거래일 이상은 볼 수 없어요.',
            vars: { n: available ?? 0, min: LONG_RANGE_FROM },
          })}
        </p>
      )}
      {isPending && <p className="muted">{t('common.loading')}</p>}
      {error && <ErrorNotice error={error} onRetry={() => void refetch()} />}

      {data && data.count === 0 && <p className="muted">{t('chart.no_data', { fallback: '아직 수집된 주가가 없어요.' })}</p>}

      {data && data.count > 0 && summary && (
        <div className={isPlaceholderData ? 'is-stale' : undefined} aria-busy={isPlaceholderData}>
          <div className="chart-readout" aria-live="polite">
            {active ? (
              <>
                <strong className="num">{formatDate(active.date)}</strong>
                <span className="num">
                  {t('chart.close', {
                    fallback: '종가 {price}',
                    vars: { price: `${formatPrice(active.close)}${t('unit.won', { fallback: '원' })}` },
                  })}
                </span>
                <span className={`num ${trendClass(active.change_pct)}`}>
                  {t('chart.day_change', { fallback: '전일 대비 {pct}', vars: { pct: formatPct(active.change_pct) } })}
                </span>
                <span className="num muted">
                  {t('chart.volume', { fallback: '거래량 {volume}', vars: { volume: formatVolume(active.volume) } })}
                </span>
                {active.open != null && active.high != null && active.low != null && (
                  <span className="num muted">
                    {t('chart.ohlc', {
                      fallback: '시 {open} · 고 {high} · 저 {low}',
                      vars: { open: formatPrice(active.open), high: formatPrice(active.high), low: formatPrice(active.low) },
                    })}
                  </span>
                )}
              </>
            ) : (
              <>
                <strong className="num">
                  {formatDate(summary.first.date)} → {formatDate(summary.last.date)}
                </strong>
                <span className={`num ${trendClass(summary.changePct)}`}>
                  {summary.changePct == null
                    ? '-'
                    : t('chart.period_change', {
                        fallback: '{pct} ({n}거래일)',
                        vars: { pct: formatPct(summary.changePct), n: data.count },
                      })}
                </span>
                <span className="num muted">
                  {t('chart.high_low', {
                    fallback: '최고 {high} · 최저 {low}',
                    vars: {
                      high: `${formatPrice(summary.high.close)}${t('unit.won', { fallback: '원' })}`,
                      low: `${formatPrice(summary.low.close)}${t('unit.won', { fallback: '원' })}`,
                    },
                  })}
                </span>
              </>
            )}
          </div>

          <svg
            ref={svgRef}
            className="price-chart"
            viewBox={`0 0 ${LAYOUT.width} ${LAYOUT.height}`}
            role="img"
            tabIndex={0}
            aria-label={t('chart.svg_aria', {
              fallback: '{name} 최근 {n}거래일 종가 추이. 왼쪽/오른쪽 화살표로 날짜를 옮길 수 있어요.',
              vars: { name: data.company_name, n: data.count },
            })}
            onPointerMove={(e) => setActiveIndex(indexFromPointer(e))}
            onPointerDown={(e) => setActiveIndex(indexFromPointer(e))}
            onPointerLeave={() => setActiveIndex(null)}
            onKeyDown={handleKeyDown}
            onBlur={() => setActiveIndex(null)}
          >
            <defs>
              <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={lineColor} stopOpacity="0.22" />
                <stop offset="100%" stopColor={lineColor} stopOpacity="0" />
              </linearGradient>
            </defs>

            {geometry.ticks.map((tick) => (
              <g key={tick.y}>
                <line
                  x1={LAYOUT.paddingLeft}
                  x2={LAYOUT.width - LAYOUT.paddingRight}
                  y1={tick.y}
                  y2={tick.y}
                  className="chart-grid"
                />
                <text x={LAYOUT.width - LAYOUT.paddingRight + 8} y={tick.y + 4} className="chart-axis">
                  {formatPrice(Math.round(tick.value))}
                </text>
              </g>
            ))}

            {geometry.bars.map((bar, i) => (
              <rect
                key={items[i].date}
                x={bar.x}
                y={bar.y}
                width={bar.width}
                height={bar.height}
                className={i === activeIndex ? 'chart-bar chart-bar-active' : 'chart-bar'}
                rx="1"
              />
            ))}

            <path d={geometry.areaPath} fill={`url(#${gradientId})`} />
            <path d={geometry.linePath} fill="none" stroke={lineColor} strokeWidth="2" strokeLinejoin="round" />
            {maLines.map((m) => (
              <path key={m.window} d={m.path} fill="none" stroke={m.color} strokeWidth="1.5" strokeLinejoin="round" opacity="0.9" />
            ))}

            {activePoint && (
              <g>
                <line
                  x1={activePoint.x}
                  x2={activePoint.x}
                  y1={LAYOUT.paddingTop}
                  y2={LAYOUT.height - LAYOUT.paddingBottom}
                  className="chart-cursor"
                />
                <circle cx={activePoint.x} cy={activePoint.y} r="4.5" fill={lineColor} className="chart-dot" />
              </g>
            )}

            <text x={LAYOUT.paddingLeft} y={LAYOUT.height - 4} className="chart-axis">
              {formatDate(summary.first.date)}
            </text>
            <text
              x={LAYOUT.width - LAYOUT.paddingRight}
              y={LAYOUT.height - 4}
              textAnchor="end"
              className="chart-axis"
            >
              {formatDate(summary.last.date)}
            </text>
          </svg>

          <dl className="return-list" aria-label={t('chart.returns_aria', { fallback: '기간별 수익률' })}>
            {RETURN_WINDOWS.map((w) => {
              const value = periodReturn(items, w.tradingDays)
              return (
                <div key={w.tradingDays}>
                  <dt className="muted small">{t('chart.range_days', { fallback: '{n}거래일', vars: { n: w.tradingDays } })}</dt>
                  <dd className={`num ${trendClass(value)}`}>{value == null ? '—' : formatPct(value)}</dd>
                </div>
              )
            })}
          </dl>
          {maLines.some((m) => !m.enough) && (
            <p className="muted small">
              {t('chart.ma_not_enough', { fallback: '이동평균은 그 기간만큼 거래일이 쌓여야 그려져요(지금 {n}거래일).', vars: { n: data.count } })}
            </p>
          )}
          {maLines.length > 0 && (
            <p className="muted small">{t('chart.ma_note', { fallback: '이동평균선은 화면에서 종가로 계산한 단순이동평균이에요.' })}</p>
          )}
          <p className="muted small chart-note">
            {t('chart.note_daily', { fallback: '일별 종가 기준이에요(장중 시세 아님).' })}{' '}
            {data.price_as_of &&
              `${t('chart.note_collected_until', { fallback: '{date}까지 수집돼 있어요.', vars: { date: formatDate(data.price_as_of) } })} `}
            {shortfall &&
              `${t('chart.note_shortfall', {
                fallback: '수집 시작일 때문에 요청한 {days}거래일보다 적은 {count}거래일만 보여드려요.',
                vars: { days: data.days, count: data.count },
              })} `}
            {t('chart.note_source', { fallback: '수집 소스(FinanceDataReader)가 준 일별 종가예요. 일부 구간은 수정주가일 수 있어요.' })}
            {data.missing_trading_days != null &&
              data.missing_trading_days > 0 &&
              ` ${t('chart.note_missing', { fallback: '이 구간에 수집되지 않은 거래일이 {n}일 있어요.', vars: { n: data.missing_trading_days } })}`}
          </p>
        </div>
      )}
    </section>
  )
}
