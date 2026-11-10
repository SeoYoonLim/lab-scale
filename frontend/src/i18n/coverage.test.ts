import { describe, expect, it } from 'vitest'
import { LOCALES } from '../api/contracts/common'
import type { Locale } from '../api/contracts/common'
import { EXTRA_MESSAGES } from './extra'
import { MESSAGES } from './index'

// 화면 코드가 쓰는 라벨 키(t('키') 호출과 { key: '키', fallback } 꼴)가 모든 언어에 번역돼 있는지 점검한다. 번역이 없으면
// 일본어·중국어 화면에서 영어(또는 한국어)가 섞여 보이므로, 새 라벨을 추가할 때 언어별 번역을 같이 넣게 강제한다.
// 백엔드 리소스(JSON)에 있으면 그것을, 없으면 extra의 언어별 객체를 본다. 한국어는 코드 안의 fallback이 원문이라 점검하지 않는다.
const sources = import.meta.glob(['/src/**/*.ts', '/src/**/*.tsx', '!/src/**/*.test.*', '!/src/i18n/**'], {
  query: '?raw',
  import: 'default',
  eager: true,
}) as Record<string, string>

function usedKeys(): Map<string, string> {
  const found = new Map<string, string>()
  const call = /\bt\(\s*(['`])([^'`]+)\1/g
  // 계약 파일의 라벨 선택 함수는 { key: 'market.kospi', fallback: '코스피' }를 돌려준다
  const object = /\bkey:\s*'([a-z_]+(?:\.[a-z_0-9]+)+)'\s*,\s*fallback:/g
  for (const [file, text] of Object.entries(sources)) {
    for (const m of text.matchAll(call)) if (!m[2].includes('${')) found.set(m[2], file)
    for (const m of text.matchAll(object)) found.set(m[1], file)
  }
  return found
}

function inResource(locale: Locale, key: string): boolean {
  let node: unknown = MESSAGES[locale]
  for (const part of key.split('.')) {
    if (node == null || typeof node !== 'object') return false
    node = (node as Record<string, unknown>)[part]
  }
  return typeof node === 'string'
}

const TRANSLATED: Locale[] = LOCALES.filter((l) => l !== 'ko-KR')

describe('라벨 번역 범위', () => {
  it('키를 실제로 찾았다(정규식이 아무것도 못 찾으면 점검이 무의미하다)', () => {
    expect(usedKeys().size).toBeGreaterThan(100)
  })

  it.each(TRANSLATED)('%s: 화면이 쓰는 모든 키에 번역이 있다', (locale) => {
    const missing = [...usedKeys()]
      .filter(([key]) => !inResource(locale, key) && !EXTRA_MESSAGES[locale]?.[key])
      .map(([key, file]) => `${key}  (${file.replace('/src/', '')})`)
    expect(missing).toEqual([])
  })

  it.each(TRANSLATED)('%s: 쓰이지 않는 추가 번역이 남아 있지 않다', (locale) => {
    const used = usedKeys()
    const stale = Object.keys(EXTRA_MESSAGES[locale] ?? {}).filter((key) => !used.has(key))
    expect(stale).toEqual([])
  })

  it('언어마다 추가 번역의 키 집합이 같다(한 언어에만 있는 키가 없다)', () => {
    const sets = TRANSLATED.map((l) => new Set(Object.keys(EXTRA_MESSAGES[l] ?? {})))
    for (const set of sets.slice(1)) {
      expect([...set].filter((k) => !sets[0].has(k))).toEqual([])
      expect([...sets[0]].filter((k) => !set.has(k))).toEqual([])
    }
  })
})
