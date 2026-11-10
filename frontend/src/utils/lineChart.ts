// 여러 선을 한 축에 그리는 차트의 좌표 계산(순수 함수). 성과 화면의 "내 포트폴리오 vs 지수"(둘 다 시작=100)에 쓴다.
// 가격 차트(utils/priceChart.ts)와 달리 거래량 막대가 없고 선이 여러 개다. x는 날짜 순서대로 균등 배치한다.
export interface LineChartLayout {
  width: number
  height: number
  paddingLeft: number
  paddingRight: number
  paddingTop: number
  paddingBottom: number
}

export interface LineSeriesInput {
  key: string
  // 날짜별 값(null이면 그 날은 선을 끊는다)
  values: (number | null)[]
}

export interface LineSeriesGeometry {
  key: string
  // 점 좌표(값이 null인 날은 null)
  points: ({ x: number; y: number } | null)[]
  // 연속된 구간마다 하나의 path(null에서 끊김)
  path: string
}

export interface LineChartGeometry {
  xs: number[]
  series: LineSeriesGeometry[]
  yMin: number
  yMax: number
  ticks: { value: number; y: number }[]
  // 값 100(기준선)의 y 좌표 - 정규화 차트에서만 의미가 있고, 범위 밖이면 null
  baselineY: number | null
}

const TICK_COUNT = 5

export function computeLineChart(
  count: number,
  inputs: LineSeriesInput[],
  layout: LineChartLayout,
  baseline: number | null = null,
): LineChartGeometry {
  const { width, height, paddingLeft, paddingRight, paddingTop, paddingBottom } = layout
  const plotWidth = width - paddingLeft - paddingRight
  const plotHeight = height - paddingTop - paddingBottom

  const all = inputs.flatMap((s) => s.values.filter((v): v is number => v != null))
  if (count === 0 || all.length === 0) {
    return { xs: [], series: inputs.map((s) => ({ key: s.key, points: [], path: '' })), yMin: 0, yMax: 0, ticks: [], baselineY: null }
  }

  let rawMin = Math.min(...all)
  let rawMax = Math.max(...all)
  // 기준선(예: 100)이 있으면 그 선이 항상 보이게 범위에 넣는다
  if (baseline != null) {
    rawMin = Math.min(rawMin, baseline)
    rawMax = Math.max(rawMax, baseline)
  }
  const span = rawMax - rawMin || Math.max(Math.abs(rawMax) * 0.02, 1)
  const yMin = rawMin - span * 0.08
  const yMax = rawMax + span * 0.08

  const xAt = (i: number) => (count === 1 ? paddingLeft + plotWidth / 2 : paddingLeft + (plotWidth * i) / (count - 1))
  const yAt = (v: number) => paddingTop + plotHeight * (1 - (v - yMin) / (yMax - yMin))
  const xs = Array.from({ length: count }, (_, i) => xAt(i))

  const series = inputs.map((input) => {
    const points = input.values.map((v, i) => (v == null ? null : { x: xs[i], y: yAt(v) }))
    let path = ''
    let drawing = false
    for (const p of points) {
      if (!p) {
        drawing = false
        continue
      }
      path += `${drawing ? 'L' : 'M'}${p.x.toFixed(1)} ${p.y.toFixed(1)} `
      drawing = true
    }
    return { key: input.key, points, path: path.trim() }
  })

  const ticks = Array.from({ length: TICK_COUNT }, (_, i) => {
    const value = yMin + ((yMax - yMin) * i) / (TICK_COUNT - 1)
    return { value, y: yAt(value) }
  })

  return { xs, series, yMin, yMax, ticks, baselineY: baseline != null ? yAt(baseline) : null }
}

export function nearestX(xs: number[], x: number): number {
  if (xs.length === 0) return -1
  let best = 0
  let bestDistance = Infinity
  xs.forEach((candidate, i) => {
    const distance = Math.abs(candidate - x)
    if (distance < bestDistance) {
      best = i
      bestDistance = distance
    }
  })
  return best
}
