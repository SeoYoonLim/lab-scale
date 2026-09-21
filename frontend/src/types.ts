// 백엔드(backend/app/models, backend/app/tools)의 필드 이름을 그대로 따른 타입.
// 백엔드 응답 형식이 바뀌면 이 파일과 src/api 만 고치면 된다.

export interface Company {
  ticker: string
  name: string
  market: string | null
  sector: string | null
}

// stock_tool 응답의 prices 항목과 동일
export interface PricePoint {
  price_date: string // YYYY-MM-DD
  close_price: number
  volume: number | null
  change_pct: number | null
}

// news_tool 응답의 news 항목과 동일
export interface NewsItem {
  title: string
  source: string | null
  published_at: string | null // ISO 8601
  url: string | null
  content: string | null
}

// disclosure_tool 응답의 disclosures 항목과 동일
export interface DisclosureItem {
  title: string
  disclosure_type: string | null
  disclosed_at: string | null // ISO 8601
  source_url: string | null
}

// 도구 응답은 found=false 일 때 message 에 사유가 담긴다. 그 외 필드는 도구마다 다르다.
export interface ToolResult {
  found?: boolean
  message?: string
  error?: string
  [key: string]: unknown
}

// tool_call_log 테이블과 동일
export interface ToolCall {
  tool_name: string
  arguments: Record<string, unknown>
  result: ToolResult
}

// research_report + tool_call_log 를 묶은 응답
export interface ResearchResponse {
  report_id: number
  question: string
  answer: string
  tool_calls: ToolCall[]
  created_at: string
}
