import { describe, expect, it } from 'vitest'
import { stripDuplicateDisclaimer } from './disclaimer'

const D = '이 서비스는 학습·시연용 모의투자와 AI 리서치입니다. AI 응답에는 오류가 있을 수 있습니다.'

describe('stripDuplicateDisclaimer(면책 중복 표시 제거)', () => {
  it('본문 끝 문단이 응답 면책과 같으면 본문에서만 뺀다(면책은 답 아래에 한 번 남는다)', () => {
    expect(stripDuplicateDisclaimer(`삼성전자 5거래일 -4.32%\n\n${D}\n`, D)).toBe('삼성전자 5거래일 -4.32%')
  })
  it('본문 끝에 면책이 없으면 그대로 둔다(LLM 경로)', () => {
    const a = '삼성전자 20거래일 -10.02%'
    expect(stripDuplicateDisclaimer(a, D)).toBe(a)
  })
  it('본문 중간의 면책이나 다른 경고·기준일 안내는 건드리지 않는다', () => {
    const a = `${D}\n\n시세가 1영업일 이전 시세(공식 종가 대기)입니다.`
    expect(stripDuplicateDisclaimer(a, D)).toBe(a)
  })
  it('문구가 조금이라도 다르면 빼지 않는다(면책을 약화하지 않는다)', () => {
    const a = `본문\n\n${D.replace('오류가', '실수가')}`
    expect(stripDuplicateDisclaimer(a, D)).toBe(a)
  })
  it('같은 글자가 문장 끝에 이어 붙은 경우(문단 경계 아님)는 빼지 않는다', () => {
    const a = `본문입니다.${D}`
    expect(stripDuplicateDisclaimer(a, D)).toBe(a)
  })
  it('면책이 없거나 비면 그대로 둔다', () => {
    expect(stripDuplicateDisclaimer('본문', '')).toBe('본문')
    expect(stripDuplicateDisclaimer('본문', null)).toBe('본문')
  })
})
