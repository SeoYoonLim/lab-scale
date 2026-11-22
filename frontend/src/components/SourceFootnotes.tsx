import { useLocale } from '../i18n/useLocale'
import type { Source } from '../types'
import { toolLabel } from '../utils/toolLabels'
import NewTabHint from './ui/NewTabHint'

// 기사·공시 제목에 한글이 있으면 한국어 원문 제목이다(백엔드는 제목을 번역하지 않는다).
const HANGUL = /[가-힣ᄀ-ᇿ㄰-㆏]/

// 답변 아래에 근거를 붙인다. 위쪽 칩은 "무엇을 근거로 했는지"를 한눈에(사용한 도구, 뉴스·공시 건수),
// 아래 각주는 번호 매긴 원문 링크다. stock_tool·market_tool처럼 근거 문서가 없는 도구는 칩으로만 보여준다.
export default function SourceFootnotes({ usedTools, sources }: { usedTools: string[]; sources: Source[] }) {
  const { t, locale } = useLocale()
  // 근거 문서가 없으면 영역 자체를 숨긴다(도구 칩만 남으면 근거가 있는 것처럼 보인다). 출처를 만들어 보여주지도 않는다.
  // 기준일 이후 자료만 있어 근거가 없다는 안내는 답변 본문에 들어 있다.
  if (usedTools.length === 0 || sources.length === 0) return null

  const newsCount = sources.filter((s) => s.type === 'news').length
  const disclosureCount = sources.filter((s) => s.type === 'disclosure').length
  // 2026-11-23: 한국어가 아닌 화면에서 한국어 원문 제목이면 원문임을 밝힌다(번역하지 않은 제목을 그대로 보여 준다). 한국어 화면에는 붙이지 않는다
  const isOriginalKorean = (title: string) => locale !== 'ko-KR' && HANGUL.test(title)
  const anyOriginal = sources.some((s) => isOriginalKorean(s.title))

  return (
    <footer className="evidence">
      <div className="evidence-title">{t('sources.title', { fallback: '주요 근거 자료' })}</div>
      <ul className="evidence-chips">
        {newsCount > 0 && (
          <li className="chip chip-strong">{t('sources.news_count', { fallback: '뉴스 {n}건', vars: { n: newsCount } })}</li>
        )}
        {disclosureCount > 0 && (
          <li className="chip chip-strong">
            {t('sources.disclosure_count', { fallback: '공시 {n}건', vars: { n: disclosureCount } })}
          </li>
        )}
        {usedTools.map((tool) => (
          <li key={tool} className="chip">
            {toolLabel(t, tool)}
          </li>
        ))}
      </ul>
      {anyOriginal && (
        <p className="muted small source-language-note">
          {t('sources.original_language_note', { fallback: '제목은 원문 언어(한국어) 그대로 표시합니다.' })}
        </p>
      )}
      {sources.length > 0 && (
        <ol className="footnotes">
          {sources.map((source, index) => {
            const original = isOriginalKorean(source.title)
            return (
              <li key={index}>
                <span className="footnote-mark num">[{index + 1}]</span>
                {source.url ? (
                  <a href={source.url} target="_blank" rel="noreferrer" lang={original ? 'ko' : undefined}>
                    {source.title}
                    <NewTabHint />
                  </a>
                ) : (
                  <span lang={original ? 'ko' : undefined}>{source.title}</span>
                )}
                <span className="muted">
                  {' '}
                  {source.type === 'news'
                    ? t('stock_feed.news_title', { fallback: '뉴스' })
                    : t('stock_feed.disclosures_title', { fallback: '공시' })}
                </span>
                {original && (
                  <span className="muted small source-original-label">
                    {' · '}
                    {t('sources.original_korean_headline', { fallback: '한국어 원문 제목' })}
                  </span>
                )}
              </li>
            )
          })}
        </ol>
      )}
    </footer>
  )
}
