import type { Source } from '../types'
import { TOOL_LABELS } from '../utils/toolLabels'

const TYPE_LABELS: Record<Source['type'], string> = {
  news: '뉴스',
  disclosure: '공시',
}

// 답변 아래에 근거를 붙인다. 위쪽 칩은 "무엇을 근거로 했는지"를 한눈에(사용한 도구, 뉴스·공시 건수),
// 아래 각주는 번호 매긴 원문 링크다. stock_tool·market_tool처럼 근거 문서가 없는 도구는 칩으로만 보여준다.
export default function SourceFootnotes({ usedTools, sources }: { usedTools: string[]; sources: Source[] }) {
  if (usedTools.length === 0) return null

  const newsCount = sources.filter((s) => s.type === 'news').length
  const disclosureCount = sources.filter((s) => s.type === 'disclosure').length

  return (
    <footer className="evidence">
      <div className="evidence-title">주요 근거 자료</div>
      <ul className="evidence-chips">
        {newsCount > 0 && <li className="chip chip-strong">뉴스 {newsCount}건</li>}
        {disclosureCount > 0 && <li className="chip chip-strong">공시 {disclosureCount}건</li>}
        {usedTools.map((t) => (
          <li key={t} className="chip">
            {TOOL_LABELS[t] ?? t}
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
              <span className="muted"> {TYPE_LABELS[source.type]}</span>
            </li>
          ))}
        </ol>
      )}
    </footer>
  )
}
