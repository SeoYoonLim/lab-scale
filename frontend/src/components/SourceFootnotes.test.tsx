import { render } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { translate } from '../i18n'
import { LocaleContext } from '../i18n/localeContext'
import type { Locale } from '../api/contracts/common'
import type { Source } from '../types'
import SourceFootnotes from './SourceFootnotes'

// 2026-11-23: 한국어가 아닌 화면에서 한국어 원문 기사·공시 제목에 원문임을 밝힌다. 한국어 화면에는 붙이지 않는다.
function view(sources: Source[], locale: Locale) {
  const value = {
    locale,
    timezone: 'Asia/Seoul',
    preferences: undefined,
    t: (key: string, options?: { fallback?: string; vars?: Record<string, string | number> }) => translate(locale, key, options),
  }
  return render(
    <LocaleContext.Provider value={value}>
      <SourceFootnotes usedTools={['news_tool']} sources={sources} />
    </LocaleContext.Provider>,
  ).container
}

const KO: Source = {
  tool: 'news_tool', type: 'news', title: 'SK하이닉스, HBM4 양산 돌입', company_names: ['SK하이닉스'], company_filter: 'SK하이닉스',
  url: 'https://example.com/a',
}
const EN: Source = { ...KO, title: 'SK hynix starts HBM4 mass production', url: 'https://example.com/b' }

describe('SourceFootnotes 원문 언어 안내', () => {
  it.each([
    ['en-US', 'Original Korean headline', 'Headlines are shown in the original source language (Korean).'],
    ['ja-JP', '韓国語の原文見出し', '見出しは原文の言語（韓国語）のまま表示しています。'],
    ['zh-CN', '韩语原文标题', '标题按原文语言（韩语）显示'],
  ] as const)('%s 화면은 한국어 제목에 원문 표시를 붙인다', (locale, label, note) => {
    const html = view([KO], locale)
    expect(html.textContent).toContain(label)
    expect(html.textContent).toContain(note)
    // 제목·링크·출처 번호는 그대로다(번역하지 않는다)
    const link = html.querySelector('a[href="https://example.com/a"]')
    expect(link?.textContent).toContain('SK하이닉스, HBM4 양산 돌입')
    expect(link?.getAttribute('lang')).toBe('ko')
    expect(html.textContent).toContain('[1]')
  })

  it('한국어 화면에는 원문 안내를 붙이지 않는다', () => {
    const html = view([KO], 'ko-KR')
    expect(html.textContent).not.toContain('원문')
    expect(html.querySelector('a')?.getAttribute('lang')).toBeNull()
    expect(html.textContent).toContain('SK하이닉스, HBM4 양산 돌입')
  })

  it('한글이 없는 제목에는 붙이지 않고, 섞여 있으면 한국어 제목에만 붙인다', () => {
    expect(view([EN], 'en-US').textContent).not.toContain('Original Korean headline')
    const html = view([EN, KO], 'en-US')
    const items = html.querySelectorAll('ol.footnotes li')
    expect(items[0].textContent).not.toContain('Original Korean headline')
    expect(items[1].textContent).toContain('Original Korean headline')
    expect(html.querySelectorAll('.source-language-note')).toHaveLength(1)
  })

  it('출처가 없으면 영역을 그리지 않는다(원문 안내도 없음)', () => {
    expect(view([], 'en-US').textContent).toBe('')
  })
})
