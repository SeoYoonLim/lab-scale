import { describe, expect, it } from 'vitest'
import koKR from './ko-KR.json'

// 화면에서 t('키')를 쓰는데 한국어 리소스에도 없고 fallback도 없으면 화면에 키 문자열("performance.chart_title")이 그대로
// 보인다. 새 라벨을 추가하다 빠뜨리는 걸 막는 점검이다. (소스 파일은 Vite의 ?raw 글롭으로 읽는다 - 노드 타입 없이도 된다.)
const sources = import.meta.glob(['/src/**/*.ts', '/src/**/*.tsx', '!/src/**/*.test.*', '!/src/i18n/**'], {
  query: '?raw',
  import: 'default',
  eager: true,
}) as Record<string, string>

function hasKey(key: string): boolean {
  let node: unknown = koKR
  for (const part of key.split('.')) {
    if (node == null || typeof node !== 'object') return false
    node = (node as Record<string, unknown>)[part]
  }
  return node !== undefined
}

describe('t() 키 점검', () => {
  it('소스 파일을 실제로 읽었다(글롭이 비어 있으면 점검이 무의미하다)', () => {
    expect(Object.keys(sources).length).toBeGreaterThan(20)
  })

  it('한국어 리소스에 없는 키는 반드시 fallback을 같이 쓴다', () => {
    const missing: string[] = []
    const pattern = /\bt\(\s*(['`])([^'`]+)\1\s*(?:,\s*\{([^}]*)\})?\s*\)/g
    for (const [file, text] of Object.entries(sources)) {
      for (const match of text.matchAll(pattern)) {
        const [, , key, options = ''] = match
        if (key.includes('${')) continue
        if (!hasKey(key) && !options.includes('fallback')) missing.push(`${file}: ${key}`)
      }
    }
    expect(missing).toEqual([])
  })
})
