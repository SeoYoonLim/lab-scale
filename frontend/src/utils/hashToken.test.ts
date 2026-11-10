import { beforeEach, describe, expect, it } from 'vitest'
import { readHashToken, resetHashTokenCache } from './hashToken'

describe('readHashToken (메일 링크의 #token=)', () => {
  beforeEach(() => {
    resetHashTokenCache()
    window.history.replaceState(null, '', '/reset-password')
  })

  it('fragment의 token을 읽고 주소창에서 지운다', () => {
    window.history.replaceState(null, '', '/reset-password?x=1#token=abc-DEF_123')
    expect(readHashToken()).toBe('abc-DEF_123')
    expect(window.location.hash).toBe('')
    expect(window.location.pathname + window.location.search).toBe('/reset-password?x=1')
  })

  it('StrictMode처럼 두 번 불러도 같은 값이다(주소는 이미 지워졌다)', () => {
    window.history.replaceState(null, '', '/verify-email#token=once')
    expect(readHashToken()).toBe('once')
    expect(readHashToken()).toBe('once')
  })

  it('다른 경로로 가면 이전 토큰을 돌려주지 않는다', () => {
    window.history.replaceState(null, '', '/verify-email#token=once')
    readHashToken()
    window.history.replaceState(null, '', '/reset-password')
    expect(readHashToken()).toBeNull()
  })

  it('fragment가 없거나 token이 없으면 null이다', () => {
    expect(readHashToken()).toBeNull()
    window.history.replaceState(null, '', '/reset-password#other=1')
    expect(readHashToken()).toBeNull()
    expect(window.location.hash).toBe('')
  })
})
