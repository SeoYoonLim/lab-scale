import type { Source } from '../types'

const TOOL_LABELS: Record<string, string> = {
  stock_tool: '주가 조회',
  news_tool: '뉴스 조회',
  disclosure_tool: '공시 조회',
  rag_search_tool: '의미 검색',
  market_tool: '시장 지수 비교',
}

// 도구 하나가 실제로 호출됐음을 보여주는 칩. 그 도구가 가져온 근거(sources)가 있으면
// 펼쳐서 제목/링크를 볼 수 있다. (stock_tool·market_tool처럼 근거 문서가 없는 도구는 "호출됨"만 표시)
export default function ToolUsage({ toolName, sources }: { toolName: string; sources: Source[] }) {
  const label = TOOL_LABELS[toolName] ?? toolName

  if (sources.length === 0) {
    return (
      <div className="tool-call tool-call-simple">
        <span className="tool-label">{label}</span>
        <span className="status status-called">호출됨</span>
      </div>
    )
  }

  return (
    <details className="tool-call">
      <summary>
        <span className="tool-label">{label}</span>
        <span className="tool-args">근거 {sources.length}건</span>
      </summary>
      <ul className="source-list">
        {sources.map((source, index) => (
          <li key={index}>
            {source.url ? (
              <a href={source.url} target="_blank" rel="noreferrer">
                {source.title}
              </a>
            ) : (
              <span>{source.title}</span>
            )}
            <div className="muted small">{source.company_names.join(', ')}</div>
          </li>
        ))}
      </ul>
    </details>
  )
}
