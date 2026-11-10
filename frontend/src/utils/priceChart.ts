import type { PriceHistoryItem } from '../types'

// 주가 추이 차트의 좌표 계산(순수 함수 - 컴포넌트에서 분리해서 단위 테스트한다).
// 차트 라이브러리를 새로 들이지 않고 SVG로 직접 그린다(선 1개 + 거래량 막대라서 충분하다).
export interface ChartLayout {
  width: number
  height: number
  // 가격 영역 아래에 거래량 막대가 차지하는 높이(전체 height 안에서)
  volumeHeight: number
  paddingLeft: number
  paddingRight: number
  paddingTop: number
  paddingBottom: number
}

export interface ChartPoint {
  x: number
  y: number
}

export interface ChartBar {
  x: number
  y: number
  width: number
  height: number
}

export interface ChartGeometry {
  points: ChartPoint[]
  bars: ChartBar[]
  linePath: string
  areaPath: string
  yMin: number
  yMax: number
  // 가로 눈금선이 그려질 가격과 y 좌표
  ticks: { value: number; y: number }[]
  // 값 -> y 좌표(이동평균 같은 보조선을 같은 축에 그릴 때 쓴다)
  yOf: (value: number) => number
}

const TICK_COUNT = 4

export function computeChart(items: PriceHistoryItem[], layout: ChartLayout): ChartGeometry {
  const { width, height, volumeHeight, paddingLeft, paddingRight, paddingTop, paddingBottom } = layout
  if (items.length === 0) {
    return { points: [], bars: [], linePath: '', areaPath: '', yMin: 0, yMax: 0, ticks: [], yOf: () => 0 }
  }

  const plotWidth = width - paddingLeft - paddingRight
  const priceBottom = height - paddingBottom - volumeHeight
  const priceHeight = priceBottom - paddingTop

  const closes = items.map((i) => i.close)
  const rawMin = Math.min(...closes)
  const rawMax = Math.max(...closes)
  // 선이 위아래 끝에 딱 붙지 않게 여백을 두고, 값이 전부 같으면(0 나누기) 임의 폭을 준다.
  const span = rawMax - rawMin || Math.max(rawMax * 0.02, 1)
  const yMin = rawMin - span * 0.08
  const yMax = rawMax + span * 0.08

  const xAt = (index: number) =>
    items.length === 1 ? paddingLeft + plotWidth / 2 : paddingLeft + (plotWidth * index) / (items.length - 1)
  const yAt = (value: number) => paddingTop + priceHeight * (1 - (value - yMin) / (yMax - yMin))

  const points = items.map((item, i) => ({ x: xAt(i), y: yAt(item.close) }))
  const linePath = points.map((p, i) => `${i === 0 ? 'M' : 'L'}${p.x.toFixed(1)} ${p.y.toFixed(1)}`).join(' ')
  const first = points[0]
  const last = points[points.length - 1]
  const areaPath = `${linePath} L${last.x.toFixed(1)} ${priceBottom.toFixed(1)} L${first.x.toFixed(1)} ${priceBottom.toFixed(1)} Z`

  const maxVolume = Math.max(0, ...items.map((i) => i.volume ?? 0))
  const slot = items.length === 1 ? plotWidth : plotWidth / (items.length - 1)
  const barWidth = Math.max(1, Math.min(14, slot * 0.7))
  const volumeBottom = height - paddingBottom
  const bars = items.map((item, i) => {
    const barHeight = maxVolume > 0 && item.volume != null ? Math.max(1, (item.volume / maxVolume) * volumeHeight) : 0
    return { x: xAt(i) - barWidth / 2, y: volumeBottom - barHeight, width: barWidth, height: barHeight }
  })

  const ticks = Array.from({ length: TICK_COUNT }, (_, i) => {
    const value = yMin + ((yMax - yMin) * i) / (TICK_COUNT - 1)
    return { value, y: yAt(value) }
  })

  return { points, bars, linePath, areaPath, yMin, yMax, ticks, yOf: yAt }
}

// 가장 가까운 데이터 인덱스(마우스/터치 x 좌표 -> 항목). x는 SVG viewBox 기준 좌표다.
export function nearestIndex(points: ChartPoint[], x: number): number {
  if (points.length === 0) return -1
  let best = 0
  let bestDistance = Infinity
  for (let i = 0; i < points.length; i += 1) {
    const distance = Math.abs(points[i].x - x)
    if (distance < bestDistance) {
      best = i
      bestDistance = distance
    }
  }
  return best
}

// 보이는 구간의 요약(시작 종가 대비 마지막 종가 등락률, 최고/최저 종가).
export function summarizeHistory(items: PriceHistoryItem[]) {
  if (items.length === 0) return null
  const first = items[0]
  const last = items[items.length - 1]
  const high = items.reduce((a, b) => (b.close > a.close ? b : a))
  const low = items.reduce((a, b) => (b.close < a.close ? b : a))
  return {
    first,
    last,
    high,
    low,
    // 구간 첫 거래일 종가 대비 마지막 종가의 등락률(%). 거래일이 1개뿐이면 비교 대상이 없어 null.
    changePct: items.length >= 2 && first.close ? Math.round((last.close / first.close - 1) * 10000) / 100 : null,
  }
}

// 단순이동평균: i번째 값은 직전 window개(자기 포함) 종가의 평균이고, window개가 모이기 전에는 null이다(앞부분을 0이나 다른 값으로
// 채우지 않는다). 백엔드가 주는 값이 아니라 화면에서 종가로 계산한 값이라서, 보이는 곳에 그렇게 표시한다.
export function movingAverage(closes: number[], window: number): (number | null)[] {
  return closes.map((_, i) => {
    if (window < 1 || i + 1 < window) return null
    let sum = 0
    for (let k = i + 1 - window; k <= i; k += 1) sum += closes[k]
    return Math.round((sum / window) * 100) / 100
  })
}

// N거래일 전 종가 대비 마지막 종가의 수익률(%). 데이터가 N+1개보다 적으면 null(요청 기간만큼 쌓이지 않은 것이다).
export function periodReturn(items: PriceHistoryItem[], tradingDays: number): number | null {
  if (items.length < tradingDays + 1) return null
  const base = items[items.length - 1 - tradingDays].close
  if (!base) return null
  return Math.round((items[items.length - 1].close / base - 1) * 10000) / 100
}
