import { describe, expect, it } from 'vitest'
import { plainSummary } from './plainSummary'

describe('plainSummary', () => {
  it('removes heading markers from a whitespace-joined answer', () => {
    expect(plainSummary('## 주가 동향 SK하이닉스의 20거래일간 주가는 -10.22% 하락했습니다. ### 원인 기사 2건')).toBe(
      '주가 동향 SK하이닉스의 20거래일간 주가는 -10.22% 하락했습니다. 원인 기사 2건',
    )
  })

  it('removes bold, code and quote markers but keeps numbers, signs and numbered items', () => {
    expect(plainSummary('**삼성전자** `190,300원` > 인용 1. 분기보고서 2. 기업설명회 -4.32% #1위')).toBe(
      '삼성전자 190,300원 인용 1. 분기보고서 2. 기업설명회 -4.32% #1위',
    )
  })

  it('leaves plain text unchanged', () => {
    const s = '삼성전자의 2026-11-17 종가는 190,300원으로, 5거래일간 -4.32%입니다.'
    expect(plainSummary(s)).toBe(s)
  })
})
