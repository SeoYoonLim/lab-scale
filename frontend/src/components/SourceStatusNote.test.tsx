import { render } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { translate } from '../i18n'
import { LocaleContext } from '../i18n/localeContext'
import type { Locale } from '../api/contracts/common'
import type { Source, SourceStatus } from '../types'
import SourceStatusNote from './SourceStatusNote'

function view(sources: Source[], status: SourceStatus | null | undefined, locale: Locale = 'ko-KR') {
  const value = {
    locale,
    timezone: 'Asia/Seoul',
    preferences: undefined,
    t: (key: string, options?: { fallback?: string; vars?: Record<string, string | number> }) => translate(locale, key, options),
  }
  return render(
    <LocaleContext.Provider value={value}>
      <SourceStatusNote sources={sources} status={status} />
    </LocaleContext.Provider>,
  ).container
}
const base = (status: string, extra: Partial<SourceStatus> = {}): SourceStatus => ({
  status, count: 0, is_error: status === 'error', empty_reason: status, message: '백엔드 한국어 원문', ...extra,
})
const SRC: Source = { tool: 'news_tool', type: 'news', title: '기사', company_names: ['삼성전자'], company_filter: '삼성전자', url: 'https://example.com/1' }

describe('SourceStatusNote(빈 출처 사유)', () => {
  it('출처가 있으면 아무것도 그리지 않는다', () => {
    expect(view([SRC], base('available')).textContent).toBe('')
  })
  it('시세·계산 답(not_applicable)·상태 없음·모르는 상태는 그리지 않는다', () => {
    expect(view([], base('not_applicable')).textContent).toBe('')
    expect(view([], null).textContent).toBe('')
    expect(view([], undefined).textContent).toBe('')
    expect(view([], base('internal_debug_code')).textContent).toBe('')
  })
  it('기준일 뒤 자료 제외·자료 없음·긴 기간·오류를 서로 다른 문구로 알린다(링크 없음, 백엔드 원문 그대로 쓰지 않음)', () => {
    const after = view([], base('excluded_after_cutoff', { excluded_after_cutoff_count: 10, excluded_unknown_time_count: 4, market_data_as_of: '2026-11-17' }))
    expect(after.textContent).toContain('2026.11.17')
    expect(after.textContent).toContain('14건')
    const none = view([], base('none_found')).textContent
    const long = view([], base('long_period')).textContent
    const err = view([], base('error'))
    expect(none).toContain('찾지 못해')
    expect(long).toContain('3개월보다 길어')
    expect(err.textContent).toContain('다시 시도')
    expect(err.querySelector('[data-status="error"]')?.className).toContain('notice')
    for (const text of [after.textContent, none, long, err.textContent]) expect(text).not.toContain('백엔드 한국어 원문')
    expect(after.querySelector('a')).toBeNull()
  })
  it('영어 화면은 영어 문구를 쓴다(한국어 백엔드 message가 섞이지 않는다)', () => {
    const text = view([], base('excluded_after_cutoff', { excluded_after_cutoff_count: 3, market_data_as_of: '2026-11-17' }), 'en-US').textContent ?? ''
    expect(text).toContain('3 items published after the market close')
    expect(text).not.toMatch(/[가-힣]/)
  })
})
