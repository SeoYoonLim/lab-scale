import type { ToolCall } from '../types'

const TOOL_LABELS: Record<string, string> = {
  stock_tool: '주가 조회',
  news_tool: '뉴스 조회',
  disclosure_tool: '공시 조회',
}

function getStatus(call: ToolCall) {
  if (call.result.error) return { label: '오류', className: 'status-error' }
  if (call.result.found === false) return { label: '데이터 없음', className: 'status-empty' }
  return { label: '성공', className: 'status-ok' }
}

export default function ToolCallChip({ call }: { call: ToolCall }) {
  const status = getStatus(call)
  const args = Object.values(call.arguments).join(' · ')

  return (
    <details className="tool-call">
      <summary>
        <span className="tool-label">{TOOL_LABELS[call.tool_name] ?? call.tool_name}</span>
        <span className="tool-args">{args}</span>
        <span className={`status ${status.className}`}>{status.label}</span>
      </summary>
      <div className="tool-body">
        <div className="tool-name">{call.tool_name}</div>
        {(call.result.message || call.result.error) && (
          <p className="tool-message">{call.result.error ?? call.result.message}</p>
        )}
        <pre>{JSON.stringify({ arguments: call.arguments, result: call.result }, null, 2)}</pre>
      </div>
    </details>
  )
}
