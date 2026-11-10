import { describe, expect, it } from 'vitest'
import { LOCALES } from '../api/contracts/common'
import { interpolate, MESSAGES, translate } from './index'

function keyPaths(node: unknown, prefix = ''): string[] {
  if (node == null || typeof node !== 'object') return [prefix]
  return Object.entries(node as Record<string, unknown>).flatMap(([k, v]) => keyPaths(v, prefix ? `${prefix}.${k}` : k))
}

describe('화면 라벨 리소스', () => {
  it('4개 언어가 같은 키를 갖는다(백엔드가 새 파일을 줄 때 하나만 빠지는 걸 막는다)', () => {
    const base = keyPaths(MESSAGES['ko-KR']).sort()
    for (const locale of LOCALES) {
      expect(keyPaths(MESSAGES[locale]).sort(), locale).toEqual(base)
    }
  })

  it('현재 언어 -> 한국어 -> fallback -> 키 순서로 찾는다', () => {
    expect(translate('en-US', 'performance.total_asset')).toBe('Total assets')
    expect(translate('ja-JP', 'common.retry')).not.toBe('다시 시도')
    expect(translate('en-US', 'no.such.key', { fallback: '대체 문구' })).toBe('대체 문구')
    expect(translate('en-US', 'no.such.key')).toBe('no.such.key')
  })

  it('백엔드 리소스에 없는 라벨은 영어 추가 라벨(평평한 "영역.이름" 키)에서 찾고, 한국어는 코드의 fallback을 쓴다', () => {
    expect(translate('en-US', 'nav.companies', { fallback: '종목 분석' })).toBe('Stocks')
    expect(translate('ja-JP', 'nav.companies', { fallback: '종목 분석' })).toBe('Stocks')
    expect(translate('ko-KR', 'nav.companies', { fallback: '종목 분석' })).toBe('종목 분석')
  })

  it('자리표시자를 채우고, 값이 없으면 그대로 둔다', () => {
    expect(interpolate('{page} / {total_pages} 페이지', { page: 2, total_pages: 5 })).toBe('2 / 5 페이지')
    expect(interpolate('총 {total}건', {})).toBe('총 {total}건')
    expect(translate('ko-KR', 'common.total_count', { vars: { total: 12 } })).toBe('총 12건')
  })
})
