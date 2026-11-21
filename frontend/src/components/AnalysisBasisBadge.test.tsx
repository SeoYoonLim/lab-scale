import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import type { Locale } from '../api/contracts/common'
import { researchResponseSchema } from '../api/schemas'
import { translate } from '../i18n'
import { LocaleContext } from '../i18n/localeContext'
import type { ResolvedInstrument } from '../types'
import AnalysisBasisBadge from './AnalysisBasisBadge'

function renderBadge(instruments: ResolvedInstrument[], locale: Locale) {
  const value = {
    locale,
    timezone: 'Asia/Seoul',
    preferences: undefined,
    t: (key: string, options?: { fallback?: string; vars?: Record<string, string | number> }) => translate(locale, key, options),
  }
  return render(
    <LocaleContext.Provider value={value}>
      <AnalysisBasisBadge instruments={instruments} />
    </LocaleContext.Provider>,
  )
}

const samsung: ResolvedInstrument = { ticker: '005930', name: '삼성전자', name_en: 'Samsung Electronics', source: 'explicit' }

describe('분석 기준 종목 표시(2026-11-21)', () => {
  it('한국어: 분석 기준: 삼성전자 (005930)', () => {
    renderBadge([samsung], 'ko-KR')
    expect(screen.getByTestId('analysis-basis').textContent).toBe('분석 기준: 삼성전자 (005930)')
  })

  it('영어: 영어 표기가 있으면 그것을 쓴다', () => {
    renderBadge([samsung], 'en-US')
    expect(screen.getByTestId('analysis-basis').textContent).toBe('Analysis based on: Samsung Electronics (005930)')
  })

  it('일본어·중국어는 답변 본문과 같은 등록명이다', () => {
    const ja = renderBadge([samsung], 'ja-JP')
    expect(screen.getByTestId('analysis-basis').textContent).toBe('分析対象: 삼성전자 (005930)')
    ja.unmount()
    renderBadge([samsung], 'zh-CN')
    expect(screen.getByTestId('analysis-basis').textContent).toBe('分析对象: 삼성전자 (005930)')
  })

  it('직전 대화의 종목을 이어받았으면 그 사실을 함께 보여준다', () => {
    renderBadge([{ ...samsung, source: 'previous_context' }], 'ko-KR')
    expect(screen.getByTestId('analysis-basis').textContent).toBe('분석 기준: 삼성전자 (005930) · 이전 질문의 종목')
  })

  it('여러 종목 비교면 모두 보여준다', () => {
    renderBadge([samsung, { ticker: '000660', name: 'SK하이닉스', name_en: 'SK hynix', source: 'explicit' }], 'en-US')
    expect(screen.getByTestId('analysis-basis').textContent).toBe('Analysis based on: Samsung Electronics (005930), SK hynix (000660)')
  })

  it('종목이 없으면(되묻기·시장 답·예전 백엔드) 그리지 않는다', () => {
    const { container } = renderBadge([], 'ko-KR')
    expect(container.textContent).toBe('')
  })

  it('응답 스키마는 새 필드가 없는 예전 응답과 있는 새 응답을 둘 다 받는다', () => {
    const base = { answer: 'a', used_tools: [], sources: [], report_id: 1, previous_report_id: null, disclaimer: 'd' }
    expect(researchResponseSchema.parse(base).resolved_instruments).toBeUndefined()
    const parsed = researchResponseSchema.parse({
      ...base,
      resolved_instruments: [{ ticker: '005930', name: '삼성전자', name_en: null, source: 'previous_context' }],
      clarification_required: false,
      clarification_reason: null,
    })
    expect(parsed.resolved_instruments?.[0].source).toBe('previous_context')
    const ask = researchResponseSchema.parse({ ...base, resolved_instruments: [], clarification_required: true, clarification_reason: 'missing_instrument' })
    expect(ask.clarification_required).toBe(true)
  })
})
