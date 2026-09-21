import type { ToolCall, ToolResult } from '../types'

const TOOL_LABELS: Record<string, string> = {
  stock_tool: '주가 조회',
  news_tool: '뉴스 조회',
  disclosure_tool: '공시 조회',
  rag_search_tool: '의미 검색',
}

function getStatus(result: ToolResult) {
  if (result.error) return { label: '오류', className: 'status-error' }
  if (result.found === false) return { label: '데이터 없음', className: 'status-empty' }
  return { label: '성공', className: 'status-ok' }
}

export default function ToolCallChip({ call }: { call: ToolCall }) {
  const label = TOOL_LABELS[call.tool_name] ?? call.tool_name

  // 백엔드가 도구 이름만 알려주는 경우: 펼쳐서 볼 상세가 없다.
  if (!call.arguments && !call.result) {
    return (
      <div className="tool-call tool-call-simple">
        <span className="tool-label">{label}</span>
        <span className="status status-called">호출됨</span>
      </div>
    )
  }

  const result = call.result ?? {}
  const status = getStatus(result)
  const args = Object.values(call.arguments ?? {}).join(' · ')

  return (
    <details className="tool-call">
      <summary>
        <span className="tool-label">{label}</span>
        <span className="tool-args">{args}</span>
        <span className={`status ${status.className}`}>{status.label}</span>
      </summary>
      <div className="tool-body">
        <div className="tool-name">{call.tool_name}</div>
        {(result.message || result.error) && (
          <p className="tool-message">{result.error ?? result.message}</p>
        )}
        <pre>{JSON.stringify({ arguments: call.arguments, result }, null, 2)}</pre>
      </div>
    </details>
  )
}
