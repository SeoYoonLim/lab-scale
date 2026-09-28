// backend/API.md 기준 타입. 필드 이름은 백엔드 응답 그대로 따른다.

// ---------- 리서치(/api/research) : 실제 백엔드 연동 대상 ----------

export interface Source {
  tool: string
  type: 'news' | 'disclosure'
  title: string
  company_names: string[]
  company_filter: string | null
  url: string | null
}

// POST /api/research 응답
export interface ResearchResponse {
  answer: string
  used_tools: string[]
  sources: Source[]
  // 저장 실패 시 null (답변 자체는 정상). previous_report_id로 이어서 조회 가능.
  report_id: number | null
  previous_report_id: number | null
}

// GET /api/research 목록 항목
export interface ReportListItem {
  report_id: number
  previous_report_id: number | null
  question: string
  summary: string | null
  // 질문에서 다룬 종목이 정확히 1개일 때만 채워짐
  company_name: string | null
  created_at: string // ISO 8601
  used_tools: string[]
}

export interface ReportList {
  total: number
  items: ReportListItem[]
}

// GET /api/research/{id} 응답 (목록 항목 + 전체 답변/근거)
export interface ReportDetail {
  report_id: number
  previous_report_id: number | null
  question: string
  answer: string
  summary: string | null
  company_name: string | null
  created_at: string
  used_tools: string[]
  sources: Source[]
}

// ---------- 종목 조회 : 아직 대응하는 백엔드 API가 없어 항상 mock ----------

export interface Company {
  ticker: string
  name: string
  market: string | null
  sector: string | null
}

export interface PricePoint {
  price_date: string // YYYY-MM-DD
  close_price: number
  volume: number | null
  change_pct: number | null
}

export interface NewsItem {
  title: string
  source: string | null
  published_at: string | null
  url: string | null
  content: string | null
}

export interface DisclosureItem {
  title: string
  disclosure_type: string | null
  disclosed_at: string | null
  source_url: string | null
}
