import { describe, expect, it } from 'vitest'
import { translate } from '../i18n'
import type { Locale } from './contracts/common'
import { codeMessage, errorText, localizedServerMessage } from './errorMessages'
import { ApiError } from './errors'

// 2026-11-21: 영어 화면에 서버의 한국어 오류 문장이 그대로 보이던 문제. 서버 code로 화면 언어 문구를 고른다(detail 문장 비교 없음).
const tFor = (locale: Locale) => (key: string, options?: { fallback?: string; vars?: Record<string, string | number> }) =>
  translate(locale, key, options)
const HANGUL = /[가-힣]/

const holdings = () => new ApiError('보유 수량이 부족합니다(매도 요청 5주, 보유 0주).', 400, undefined, undefined, undefined, 'INSUFFICIENT_HOLDINGS')

describe('서버 오류 code별 화면 문구', () => {
  it('한국어 화면은 서버 문장(수량까지 담긴 detail)을 그대로 쓴다', () => {
    expect(localizedServerMessage(holdings(), tFor('ko-KR'), 'ko-KR')).toBe('보유 수량이 부족합니다(매도 요청 5주, 보유 0주).')
  })

  it.each([
    ['en-US', 'Insufficient holdings.'],
    ['ja-JP', '保有数量が不足しています。'],
    ['zh-CN', '持仓数量不足。'],
  ] as const)('보유 수량 부족은 %s 문구로 바뀐다', (locale, expected) => {
    expect(localizedServerMessage(holdings(), tFor(locale), locale)).toBe(expected)
  })

  it.each([
    ['INSUFFICIENT_CASH', 'Insufficient cash balance.'],
    ['INVALID_ORDER_QUANTITY', 'The order quantity is invalid. Enter a whole number of at least 1 share.'],
    ['UNSUPPORTED_ORDER_TYPE', 'This order type is not supported. Only buy and sell orders are available.'],
    ['STOCK_NOT_FOUND', 'Stock not found. Please check the stock name or ticker.'],
    ['AUTHENTICATION_REQUIRED', 'Authentication is required. Please log in again.'],
  ])('영어 화면: %s', (code, expected) => {
    const err = new ApiError('한국어 서버 문장', 400, undefined, undefined, undefined, code)
    expect(localizedServerMessage(err, tFor('en-US'), 'en-US')).toBe(expected)
  })

  it('일본어·중국어 화면의 현금 부족·수량 오류도 그 언어다', () => {
    for (const code of ['INSUFFICIENT_CASH', 'INVALID_ORDER_QUANTITY']) {
      for (const locale of ['ja-JP', 'zh-CN'] as const) {
        const text = localizedServerMessage(new ApiError('한국어', 400, undefined, undefined, undefined, code), tFor(locale), locale)
        expect(text).not.toMatch(HANGUL)
        expect(text.length).toBeGreaterThan(3)
      }
    }
  })

  it('모르는 code면 그 언어의 상태 코드별 일반 문구다(한국어 detail을 보이지 않는다)', () => {
    const err = new ApiError('알 수 없는 오류입니다.', 400, undefined, undefined, undefined, 'SOMETHING_NEW')
    expect(codeMessage('SOMETHING_NEW', tFor('en-US'))).toBeNull()
    const text = localizedServerMessage(err, tFor('en-US'), 'en-US')
    expect(text).not.toMatch(HANGUL)
    expect(text).toBe(translate('en-US', 'errors.400'))
  })

  it('code 없는 예전 응답: 한국어 detail은 일반 문구로, 그 언어로 온 detail은 그대로', () => {
    expect(localizedServerMessage(new ApiError('잔고가 부족합니다.', 400), tFor('en-US'), 'en-US')).toBe(translate('en-US', 'errors.400'))
    expect(localizedServerMessage(new ApiError('No holdings to diagnose.', 400), tFor('en-US'), 'en-US')).toBe('No holdings to diagnose.')
    expect(localizedServerMessage(new ApiError('잔고가 부족합니다.', 400), tFor('ko-KR'), 'ko-KR')).toBe('잔고가 부족합니다.')
  })

  it('인증 오류(401)와 네트워크 오류도 화면 언어다', () => {
    const auth = new ApiError('인증이 필요합니다. 다시 로그인해주세요.', 401, undefined, undefined, undefined, 'AUTHENTICATION_REQUIRED')
    expect(errorText(auth, tFor('en-US'), 'en-US', 'fallback')).toBe('Authentication is required. Please log in again.')
    expect(errorText(new TypeError('Failed to fetch'), tFor('en-US'), 'en-US', 'fallback')).toBe(translate('en-US', 'errors.network'))
    expect(errorText(new Error('한국어 오류'), tFor('en-US'), 'en-US', 'Order failed.')).toBe('Order failed.')
  })
})
