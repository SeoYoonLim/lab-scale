import { useLocale } from '../i18n/useLocale'
import type { Source } from '../types'
import { toolLabel } from '../utils/toolLabels'

// 답변 아래에 근거를 붙인다. 위쪽 칩은 "무엇을 근거로 했는지"를 한눈에(사용한 도구, 뉴스·공시 건수),
// 아래 각주는 번호 매긴 원문 링크다. stock_tool·market_tool처럼 근거 문서가 없는 도구는 칩으로만 보여준다.
export default function SourceFootnotes({ usedTools, sources }: { usedTools: string[]; sources: Source[] }) {
  const { t } = useLocale()
  if (usedTools.length === 0) return null

  const newsCount = sources.filter((s) => s.type === 'news').length
  const disclosureCount = sources.filter((s) => s.type === 'disclosure').length

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
      {sources.length > 0 && (
        <ol className="footnotes">
          {sources.map((source, index) => (
            <li key={index}>
              <span className="footnote-mark num">[{index + 1}]</span>
              {source.url ? (
                <a href={source.url} target="_blank" rel="noreferrer">
                  {source.title}
                </a>
              ) : (
                <span>{source.title}</span>
              )}
              <span className="muted">
                {' '}
                {source.type === 'news'
                  ? t('stock_feed.news_title', { fallback: '뉴스' })
                  : t('stock_feed.disclosures_title', { fallback: '공시' })}
              </span>
            </li>
          ))}
        </ol>
      )}
    </footer>
  )
}
