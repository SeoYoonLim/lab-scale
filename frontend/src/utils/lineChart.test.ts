import { describe, expect, it } from 'vitest'
import { computeLineChart, nearestX } from './lineChart'
import type { LineChartLayout } from './lineChart'

const LAYOUT: LineChartLayout = { width: 400, height: 200, paddingLeft: 10, paddingRight: 10, paddingTop: 10, paddingBottom: 10 }

describe('computeLineChart', () => {
  it('데이터가 없으면 빈 기하를 준다', () => {
    expect(computeLineChart(0, [{ key: 'a', values: [] }], LAYOUT).series[0].path).toBe('')
  })

  it('두 선이 같은 축을 쓰고 값이 클수록 y가 작다(위쪽)', () => {
    const g = computeLineChart(
      3,
      [
        { key: 'portfolio', values: [100, 105, 110] },
        { key: 'benchmark', values: [100, 95, 90] },
      ],
      LAYOUT,
    )
    const [portfolio, benchmark] = g.series
    expect(portfolio.points[2]!.y).toBeLessThan(portfolio.points[0]!.y)
    expect(benchmark.points[2]!.y).toBeGreaterThan(benchmark.points[0]!.y)
    // 시작값(100)은 두 선이 같은 y에서 출발한다
    expect(portfolio.points[0]!.y).toBeCloseTo(benchmark.points[0]!.y)
    expect(g.xs[0]).toBe(10)
    expect(g.xs[2]).toBe(390)
  })

  it('기준선 값이 데이터 범위 밖이어도 그 선이 보이게 범위에 넣는다', () => {
    const g = computeLineChart(2, [{ key: 'a', values: [110, 120] }], LAYOUT, 100)
    expect(g.baselineY).not.toBeNull()
    expect(g.baselineY!).toBeLessThanOrEqual(190)
    expect(g.baselineY!).toBeGreaterThanOrEqual(10)
  })

  it('값이 null인 날은 선을 끊는다(0으로 그리지 않는다)', () => {
    const g = computeLineChart(4, [{ key: 'a', values: [1, 2, null, 4] }], LAYOUT)
    expect(g.series[0].points[2]).toBeNull()
    expect(g.series[0].path.match(/M/g)).toHaveLength(2)
  })

  it('모든 값이 같아도(0 나누기) 유한한 좌표를 준다', () => {
    const g = computeLineChart(2, [{ key: 'a', values: [100, 100] }], LAYOUT)
    expect(g.series[0].points.every((p) => p != null && Number.isFinite(p.y))).toBe(true)
  })
})

describe('nearestX', () => {
  it('가장 가까운 x의 인덱스를 준다', () => {
    expect(nearestX([10, 200, 390], 180)).toBe(1)
    expect(nearestX([], 5)).toBe(-1)
  })
})
