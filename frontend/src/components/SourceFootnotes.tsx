import type { Source } from '../types'
import { TOOL_LABELS } from '../utils/toolLabels'

const TYPE_LABELS: Record<Source['type'], string> = {
  news: '뉴스',
  disclosure: '공시',
}

// 답변 아래에 근거를 각주처럼 붙인다. stock_tool·market_tool처럼 근거 문서가
// 없는 도구는 "사용한 도구" 한 줄로만, 뉴스·공시·RAG 근거는 번호 매긴 각주로 보여준다.
export default function SourceFootnotes({ usedTools, sources }: { usedTools: string[]; sources: Source[] }) {
  if (usedTools.length === 0) return null

  return (
    <footer className="evidence">
      <div className="evidence-tools muted small">
        도구: {usedTools.map((t) => TOOL_LABELS[t] ?? t).join(', ')}
      </div>
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
