import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { useId, useRef, useState } from 'react'
import type { KeyboardEvent, PointerEvent } from 'react'
import { api } from '../api'
import { queryKeys } from '../api/queryKeys'
import { computeChart, nearestIndex, summarizeHistory } from '../utils/priceChart'
import type { ChartLayout } from '../utils/priceChart'
import { formatDate, formatPct, formatPrice, formatVolume, trendClass } from '../utils/format'

// days는 달력 일이 아니라 "최근 N거래일"이다(backend/API.md). 데이터 시작일 때문에 실제 개수(count)가
// 더 적을 수 있어서, 아래에서 항상 count를 기준으로 그리고 모자라면 그 사실을 알려준다.
const RANGES = [
  { days: 20, label: '20거래일' },
  { days: 60, label: '60거래일' },
  { days: 90, label: '최대(90)' },
] as const

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
  const [days, setDays] = useState<number>(60)
  const [activeIndex, setActiveIndex] = useState<number | null>(null)
  const svgRef = useRef<SVGSVGElement>(null)
  const gradientId = useId()

  // 일별 저장 데이터라 폴링하지 않는다(현재가는 위쪽 카드의 realtime-price가 3초마다 갱신).
  const { data, error, isPending, isPlaceholderData } = useQuery({
    queryKey: queryKeys.priceHistory(ticker, days),
    queryFn: () => api.getPriceHistory(ticker, days),
    placeholderData: keepPreviousData,
    retry: false,
    staleTime: 60_000,
  })

  const items = data?.items ?? []
  const geometry = computeChart(items, LAYOUT)
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

  return (
    <section className="card price-chart-card" aria-labelledby="price-chart-heading">
      <div className="card-head">
        <h2 id="price-chart-heading">주가 추이</h2>
        <div className="segmented" role="group" aria-label="조회 기간">
          {RANGES.map((range) => (
            <button
              key={range.days}
              type="button"
              className={days === range.days ? 'seg active' : 'seg'}
              aria-pressed={days === range.days}
              onClick={() => {
                setDays(range.days)
                setActiveIndex(null)
              }}
            >
              {range.label}
            </button>
          ))}
        </div>
      </div>

      {isPending && <p className="muted">불러오는 중…</p>}
      {error && <p className="notice notice-error">{error.message}</p>}

      {data && data.count === 0 && <p className="muted">아직 수집된 주가가 없어요.</p>}

      {data && data.count > 0 && summary && (
        <div className={isPlaceholderData ? 'is-stale' : undefined} aria-busy={isPlaceholderData}>
          <div className="chart-readout" aria-live="polite">
            {active ? (
              <>
                <strong className="num">{formatDate(active.date)}</strong>
                <span className="num">종가 {formatPrice(active.close)}원</span>
                <span className={`num ${trendClass(active.change_pct)}`}>전일 대비 {formatPct(active.change_pct)}</span>
                <span className="num muted">거래량 {formatVolume(active.volume)}</span>
                {active.open != null && active.high != null && active.low != null && (
                  <span className="num muted">
                    시 {formatPrice(active.open)} · 고 {formatPrice(active.high)} · 저 {formatPrice(active.low)}
                  </span>
                )}
              </>
            ) : (
              <>
                <strong className="num">
                  {formatDate(summary.first.date)} → {formatDate(summary.last.date)}
                </strong>
                <span className={`num ${trendClass(summary.changePct)}`}>
                  {summary.changePct == null ? '-' : `${formatPct(summary.changePct)} (${data.count}거래일)`}
                </span>
                <span className="num muted">
                  최고 {formatPrice(summary.high.close)}원 · 최저 {formatPrice(summary.low.close)}원
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
            aria-label={`${data.company_name} 최근 ${data.count}거래일 종가 추이. 왼쪽/오른쪽 화살표로 날짜를 옮길 수 있어요.`}
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

          <p className="muted small chart-note">
            일별 종가 기준이에요(장중 시세 아님). {data.price_as_of && `${formatDate(data.price_as_of)}까지 수집돼 있어요. `}
            {shortfall &&
              `수집 시작일 때문에 요청한 ${data.days}거래일보다 적은 ${data.count}거래일만 보여드려요. `}
            분할·배당 보정은 하지 않은 값이에요.
          </p>
        </div>
      )}
    </section>
  )
}
