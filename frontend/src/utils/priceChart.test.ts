import { describe, expect, it } from 'vitest'
import type { PriceHistoryItem } from '../types'
import { computeChart, nearestIndex, summarizeHistory } from './priceChart'
import type { ChartLayout } from './priceChart'

const LAYOUT: ChartLayout = {
  width: 400,
  height: 200,
  volumeHeight: 40,
  paddingLeft: 10,
  paddingRight: 10,
  paddingTop: 10,
  paddingBottom: 10,
}

function item(date: string, close: number, volume: number | null = 100): PriceHistoryItem {
  return { date, close, volume, change_pct: null, open: null, high: null, low: null }
}

describe('computeChart', () => {
  it('항목이 없으면 빈 기하를 돌려준다(주가가 아직 없는 종목: items=[])', () => {
    expect(computeChart([], LAYOUT)).toMatchObject({ points: [], bars: [], linePath: '', areaPath: '' })
  })

  it('x는 날짜 순서대로 균등하게, 높은 종가일수록 y가 작다(위쪽)', () => {
    const { points } = computeChart([item('a', 100), item('b', 200), item('c', 150)], LAYOUT)
    expect(points[0].x).toBe(10)
    expect(points[2].x).toBe(390)
    expect(points[1].x).toBeCloseTo(200)
    expect(points[1].y).toBeLessThan(points[2].y)
    expect(points[2].y).toBeLessThan(points[0].y)
  })

  it('모든 종가가 같아도 0으로 나누지 않고 유한한 좌표를 준다', () => {
    const { points, ticks } = computeChart([item('a', 100), item('b', 100)], LAYOUT)
    expect(points.every((p) => Number.isFinite(p.y))).toBe(true)
    expect(ticks.every((t) => Number.isFinite(t.y))).toBe(true)
  })

  it('항목이 1개여도(데이터 시작 직후) 가운데에 점 하나를 그린다', () => {
    const { points, bars } = computeChart([item('a', 100)], LAYOUT)
    expect(points).toHaveLength(1)
    expect(points[0].x).toBe(200)
    expect(bars).toHaveLength(1)
  })

  it('거래량이 없는(null) 날은 막대 높이가 0이고, 가장 큰 날이 volumeHeight를 채운다', () => {
    const { bars } = computeChart([item('a', 100, null), item('b', 100, 50), item('c', 100, 100)], LAYOUT)
    expect(bars[0].height).toBe(0)
    expect(bars[2].height).toBe(40)
    expect(bars[1].height).toBeCloseTo(20)
  })
})

describe('nearestIndex', () => {
  it('x와 가장 가까운 점의 인덱스를 돌려준다', () => {
    const { points } = computeChart([item('a', 1), item('b', 2), item('c', 3)], LAYOUT)
    expect(nearestIndex(points, 0)).toBe(0)
    expect(nearestIndex(points, 190)).toBe(1)
    expect(nearestIndex(points, 999)).toBe(2)
    expect(nearestIndex([], 5)).toBe(-1)
  })
})

describe('summarizeHistory', () => {
  it('첫 종가 대비 마지막 종가의 등락률과 최고/최저를 계산한다', () => {
    const s = summarizeHistory([item('a', 100), item('b', 130), item('c', 110)])
    expect(s?.changePct).toBe(10)
    expect(s?.high.date).toBe('b')
    expect(s?.low.date).toBe('a')
  })

  it('거래일이 1개뿐이면 등락률은 null, 없으면 전체가 null', () => {
    expect(summarizeHistory([item('a', 100)])?.changePct).toBeNull()
    expect(summarizeHistory([])).toBeNull()
  })
})
