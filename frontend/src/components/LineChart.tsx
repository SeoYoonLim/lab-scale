import { useRef, useState } from 'react'
import type { KeyboardEvent, PointerEvent, ReactNode } from 'react'
import { computeLineChart, nearestX } from '../utils/lineChart'
import type { LineChartLayout } from '../utils/lineChart'
import { formatTradingDate } from '../utils/format'

const LAYOUT: LineChartLayout = { width: 720, height: 280, paddingLeft: 8, paddingRight: 64, paddingTop: 14, paddingBottom: 24 }

export interface LineChartSeries {
  key: string
  label: string
  // CSS 색(변수 허용)
  color: string
  values: (number | null)[]
  // 값이 추정(시세가 빠진 날을 직전 종가로 평가)인 날 - 속이 빈 점으로 구분한다
  estimated?: boolean[]
}

// 여러 선을 한 축에 그리는 SVG 차트. 마우스·터치로 가리키거나, 포커스한 뒤 좌우 화살표로 날짜를 옮기면
// readout(index)가 그 날 값을 보여준다. 기준선(baseline)이 있으면(시작=100) 점선으로 그린다.
export default function LineChart({
  dates,
  series,
  baseline = null,
  formatValue,
  ariaLabel,
  readout,
}: {
  dates: string[]
  series: LineChartSeries[]
  baseline?: number | null
  formatValue: (value: number) => string
  ariaLabel: string
  readout: (index: number | null) => ReactNode
}) {
  const [active, setActive] = useState<number | null>(null)
  const svgRef = useRef<SVGSVGElement>(null)
  const geometry = computeLineChart(dates.length, series.map((s) => ({ key: s.key, values: s.values })), LAYOUT, baseline)

  function indexFromPointer(event: PointerEvent<SVGSVGElement>): number | null {
    const rect = svgRef.current?.getBoundingClientRect()
    if (!rect || rect.width === 0) return null
    const x = ((event.clientX - rect.left) / rect.width) * LAYOUT.width
    const index = nearestX(geometry.xs, x)
    return index >= 0 ? index : null
  }

  function handleKeyDown(event: KeyboardEvent<SVGSVGElement>) {
    if (dates.length === 0) return
    if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
      event.preventDefault()
      const step = event.key === 'ArrowLeft' ? -1 : 1
      setActive((prev) => Math.min(dates.length - 1, Math.max(0, (prev ?? dates.length - 1) + step)))
    } else if (event.key === 'Escape') {
      setActive(null)
    }
  }

  if (dates.length === 0) return null
  const cursorX = active != null ? geometry.xs[active] : null

  return (
    <div className="line-chart">
      <div className="chart-readout" aria-live="polite">
        {readout(active)}
      </div>
      <svg
        ref={svgRef}
        className="price-chart"
        viewBox={`0 0 ${LAYOUT.width} ${LAYOUT.height}`}
        role="img"
        tabIndex={0}
        aria-label={ariaLabel}
        onPointerMove={(e) => setActive(indexFromPointer(e))}
        onPointerDown={(e) => setActive(indexFromPointer(e))}
        onPointerLeave={() => setActive(null)}
        onKeyDown={handleKeyDown}
        onBlur={() => setActive(null)}
      >
        {geometry.ticks.map((tick) => (
          <g key={tick.y}>
            <line x1={LAYOUT.paddingLeft} x2={LAYOUT.width - LAYOUT.paddingRight} y1={tick.y} y2={tick.y} className="chart-grid" />
            <text x={LAYOUT.width - LAYOUT.paddingRight + 8} y={tick.y + 4} className="chart-axis">
              {formatValue(tick.value)}
            </text>
          </g>
        ))}

        {geometry.baselineY != null && (
          <line
            x1={LAYOUT.paddingLeft}
            x2={LAYOUT.width - LAYOUT.paddingRight}
            y1={geometry.baselineY}
            y2={geometry.baselineY}
            className="chart-baseline"
          />
        )}

        {series.map((s, si) => (
          <g key={s.key}>
            <path d={geometry.series[si].path} fill="none" stroke={s.color} strokeWidth="2" strokeLinejoin="round" />
            {s.estimated?.map((isEstimated, i) => {
              const p = geometry.series[si].points[i]
              return isEstimated && p ? (
                <circle key={i} cx={p.x} cy={p.y} r="3.5" fill="var(--surface)" stroke={s.color} strokeWidth="1.5" />
              ) : null
            })}
          </g>
        ))}

        {cursorX != null && active != null && (
          <g>
            <line x1={cursorX} x2={cursorX} y1={LAYOUT.paddingTop} y2={LAYOUT.height - LAYOUT.paddingBottom} className="chart-cursor" />
            {series.map((s, si) => {
              const p = geometry.series[si].points[active]
              return p ? <circle key={s.key} cx={p.x} cy={p.y} r="4.5" fill={s.color} className="chart-dot" /> : null
            })}
          </g>
        )}

        <text x={LAYOUT.paddingLeft} y={LAYOUT.height - 4} className="chart-axis">
          {formatTradingDate(dates[0])}
        </text>
        <text x={LAYOUT.width - LAYOUT.paddingRight} y={LAYOUT.height - 4} textAnchor="end" className="chart-axis">
          {formatTradingDate(dates.at(-1))}
        </text>
      </svg>

      <ul className="chart-legend">
        {series.map((s) => (
          <li key={s.key}>
            <span className="legend-swatch" style={{ background: s.color }} aria-hidden="true" />
            {s.label}
          </li>
        ))}
      </ul>
    </div>
  )
}
