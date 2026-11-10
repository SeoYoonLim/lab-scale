import { render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it } from 'vitest'
import { ApiError } from '../../api/errors'
import { translate } from '../../i18n'
import { LocaleContext } from '../../i18n/localeContext'
import { setUiLocale } from '../../i18n/uiLocale'
import { formatWon } from '../../utils/format'
import ErrorNotice from './ErrorNotice'
import type { Locale } from '../../api/contracts/common'

function renderNotice(error: unknown, locale: Locale) {
  const value = {
    locale,
    timezone: 'Asia/Seoul',
    preferences: undefined,
    t: (key: string, options?: { fallback?: string; vars?: Record<string, string | number> }) => translate(locale, key, options),
  }
  return render(
    <LocaleContext.Provider value={value}>
      <ErrorNotice error={error} />
    </LocaleContext.Provider>,
  )
}

describe('ErrorNotice 언어 처리', () => {
  it('서버가 준 문장(한국어)은 언어와 상관없이 그대로 보여준다', () => {
    renderNotice(new ApiError('이미 사용 중인 아이디입니다.', 409), 'ja-JP')
    expect(screen.getByRole('alert').textContent).toContain('이미 사용 중인 아이디입니다.')
  })

  it.each([
    ['ko-KR', '너무 오래 걸려서'],
    ['en-US', 'took too long'],
    ['ja-JP', '時間がかかりすぎた'],
    ['zh-CN', '耗时过长'],
  ] as const)('클라이언트가 만든 시간 초과 오류는 화면 언어로 보여준다(%s)', (locale, expected) => {
    renderNotice(new ApiError('요청이 너무 오래 걸려서 중단했어요. 네트워크를 확인하고 다시 시도해주세요.', 0, undefined, 'timeout'), locale)
    expect(screen.getByRole('alert').textContent).toContain(expected)
  })

  it('응답 형식 오류와 본문 없는 실패(상태 코드 포함)도 번역된다', () => {
    const first = renderNotice(new ApiError('서버 응답 형식이 올바르지 않아요. 잠시 후 다시 시도해주세요.', 502, undefined, 'schema'), 'en-US')
    expect(screen.getByRole('alert').textContent).toContain('unexpected format')
    first.unmount()
    renderNotice(new ApiError('요청에 실패했어요. (500)', 500, undefined, 'request_failed'), 'ja-JP')
    expect(screen.getByRole('alert').textContent).toContain('リクエストに失敗しました。(500)')
  })

  it('429는 서버 문장은 그대로 두고 "n초 뒤 다시"만 화면 언어로 붙인다', () => {
    renderNotice(new ApiError('요청이 많습니다. (12초 뒤 다시 시도해주세요)', 429, 12, undefined, '요청이 많습니다.'), 'en-US')
    const text = screen.getByRole('alert').textContent ?? ''
    expect(text).toContain('요청이 많습니다.')
    expect(text).toContain('try again in 12 seconds')
    expect(text).not.toContain('초 뒤')
  })

  it('브라우저가 서버에 닿지 못하면(fetch의 TypeError) 영어 브라우저 문장 대신 번역된 안내를 보여준다', () => {
    renderNotice(new TypeError('Failed to fetch'), 'zh-CN')
    const text = screen.getByRole('alert').textContent ?? ''
    expect(text).not.toContain('Failed to fetch')
    expect(text.length).toBeGreaterThan(0)
  })
})

describe('formatWon 단위는 화면 언어를 따른다', () => {
  beforeEach(() => setUiLocale('ko-KR'))

  it.each([
    ['ko-KR', '1,234원'],
    ['en-US', '1,234 KRW'],
    ['ja-JP', '1,234ウォン'],
    ['zh-CN', '1,234韩元'],
  ] as const)('%s', (locale, expected) => {
    setUiLocale(locale)
    expect(formatWon(1234)).toBe(expected)
    expect(formatWon(null)).toBe('—')
  })
})
