import { useLocale } from '../i18n/useLocale'

interface Label {
  key: string
  fallback: string
}

// 백엔드는 한 번의 요청으로 답을 돌려줘서(스트리밍 없음) "지금 몇 단계"인지는 알 수 없다.
// 그래서 진행률처럼 보이는 가짜 단계 표시 대신, AI Agent가 보통 확인하는 순서를 안내하고
// 막대는 끝을 알 수 없는(indeterminate) 모양으로 둔다.
const STEPS: { title: Label; desc: Label }[] = [
  {
    title: { key: 'progress.step1_title', fallback: '질문 분석' },
    desc: { key: 'progress.step1_desc', fallback: '의도 파악, 종목·키워드 추출' },
  },
  {
    title: { key: 'datatools.price_title', fallback: '주가 데이터' },
    desc: { key: 'progress.step2_desc', fallback: '등락률, 거래량 조회' },
  },
  {
    title: { key: 'progress.step3_title', fallback: '관련 뉴스' },
    desc: { key: 'progress.step3_desc', fallback: '주요 기사 검색' },
  },
  {
    title: { key: 'progress.step4_title', fallback: '기업 공시' },
    desc: { key: 'progress.step4_desc', fallback: '공시 정보 확인' },
  },
  {
    title: { key: 'progress.step5_title', fallback: '시장 비교' },
    desc: { key: 'progress.step5_desc', fallback: '지수 대비 흐름 확인' },
  },
  {
    title: { key: 'progress.step6_title', fallback: '종합 정리' },
    desc: { key: 'progress.step6_desc', fallback: '근거를 모아 답변 작성' },
  },
]

export default function ResearchProgress() {
  const { t } = useLocale()
  return (
    <div className="progress-card" role="status" aria-live="polite">
      <div className="progress-title">{t('progress.title', { fallback: 'AI Agent가 리서치를 진행하고 있어요…' })}</div>
      <ol className="progress-steps">
        {STEPS.map((step, index) => (
          <li key={step.title.key} style={{ animationDelay: `${index * 0.35}s` }}>
            <span className="progress-step-no num">{index + 1}</span>
            <span className="progress-step-title">{t(step.title.key, { fallback: step.title.fallback })}</span>
            <span className="progress-step-desc muted small">{t(step.desc.key, { fallback: step.desc.fallback })}</span>
          </li>
        ))}
      </ol>
      <div className="progress-bar" aria-hidden="true">
        <i />
      </div>
      <p className="muted small">{t('progress.note', { fallback: '질문에 따라 필요한 도구만 골라 쓰고, 보통 10~60초 걸려요.' })}</p>
    </div>
  )
}
