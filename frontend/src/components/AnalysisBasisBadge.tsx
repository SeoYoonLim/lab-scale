import { useLocale } from '../i18n/useLocale'
import type { ResolvedInstrument } from '../types'

// 리서치 답변이 실제로 분석한 종목(백엔드 resolved_instruments, 2026-11-21). "분석 기준: 삼성전자 (005930)".
// - 종목이 없으면(시장·용어 답, 되묻기 답, 예전 백엔드) 아무것도 그리지 않는다.
// - 영어 화면은 영어 표기(name_en)가 있으면 그것을, 없으면 등록명을 쓴다. 다른 언어는 답변 본문과 같은 등록명이다.
// - 직전 대화의 종목을 이어받았으면(source=previous_context) 그 사실을 함께 보여준다(사용자가 다른 종목으로 바꾼 줄 알지 않게).
export default function AnalysisBasisBadge({ instruments }: { instruments: ResolvedInstrument[] }) {
  const { t, locale } = useLocale()
  if (instruments.length === 0) return null
  const label = t('research.basis_label', { fallback: '분석 기준' })
  const previous = t('research.basis_previous', { fallback: '이전 질문의 종목' })
  return (
    <p className="analysis-basis muted small" data-testid="analysis-basis">
      <span className="analysis-basis-label">{label}: </span>
      {instruments.map((item, i) => {
        const name = locale === 'en-US' && item.name_en ? item.name_en : item.name
        return (
          <span key={`${item.ticker}-${i}`} className="analysis-basis-item" data-source={item.source}>
            {i > 0 && ', '}
            {name} ({item.ticker})
            {item.source === 'previous_context' && <span className="analysis-basis-note"> · {previous}</span>}
          </span>
        )
      })}
    </p>
  )
}
