import { describe, expect, it } from 'vitest'
import { ApiError, classifyError, isRetryable } from './errors'

describe('classifyError / isRetryable', () => {
  it('상태 코드를 화면 처리 종류로 나눈다(핸드오프 6절)', () => {
    expect(classifyError(new ApiError('x', 401))).toBe('auth')
    expect(classifyError(new ApiError('x', 404))).toBe('not_found')
    expect(classifyError(new ApiError('x', 409))).toBe('conflict')
    expect(classifyError(new ApiError('x', 422))).toBe('validation')
    expect(classifyError(new ApiError('x', 429, 5))).toBe('rate_limit')
    expect(classifyError(new ApiError('x', 0))).toBe('timeout')
    expect(classifyError(new ApiError('x', 503))).toBe('server')
    expect(classifyError(new Error('boom'))).toBe('unknown')
  })

  it('다시 시도하면 될 수 있는 오류(서버·시간 초과·한도)만 재시도 대상이다', () => {
    expect(isRetryable(new ApiError('x', 503))).toBe(true)
    expect(isRetryable(new ApiError('x', 0))).toBe(true)
    expect(isRetryable(new ApiError('x', 429))).toBe(true)
    expect(isRetryable(new ApiError('x', 404))).toBe(false)
    expect(isRetryable(new ApiError('x', 422))).toBe(false)
    expect(isRetryable(new ApiError('x', 401))).toBe(false)
  })
})
