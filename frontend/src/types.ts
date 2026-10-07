// backend/API.md 기준 타입. 필드 이름은 백엔드 응답 그대로 따른다.

// ---------- 인증(/api/auth) ----------

export interface User {
  id: number
  username: string
}

export interface AuthResponse {
  user: User
  access_token: string
  token_type: string
}

// ---------- 리서치(/api/research) : 로그인 필요 ----------

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
  disclaimer: string
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
  disclaimer: string
}

// ---------- 실시간 시세(/api/stocks/{ticker}/realtime-price) : 공개 ----------

export interface RealtimePrice {
  ticker: string
  company_name: string
  current_price: number
  // 폴백(장외·소스 실패)일 때는 항상 null
  change_amount: number | null
  change_pct: number | null
  as_of: string // 실시간: 체결 시각(ISO, KST) / 폴백: 종가 날짜(YYYY-MM-DD)
  queried_at: string
  is_realtime: boolean
  source: 'naver' | 'fallback'
  corrected_from: string | null
}

// ---------- 종목 검색(/api/companies) : 공개 ----------

export interface Company {
  ticker: string
  name: string
  market: string | null
  sector: string | null
  // DB에 저장된 가장 최근 종가(일별, 실시간 아님)
  latest_close: number | null
  change_pct: number | null
}

// ---------- 오늘의 관심 종목(/api/discovery/trending) : 공개 ----------

export type TrendingCategory = 'gainers' | 'losers' | 'volume_surge'

export interface TrendingItem {
  ticker: string
  company_name: string
  close_price: number
  change_pct: number | null
  volume: number | null
  avg_volume: number | null
  volume_ratio: number | null
}

export interface TrendingResponse {
  category: TrendingCategory
  price_date: string
  window_days: number
  items: TrendingItem[]
}

// ---------- 관심종목(/api/watchlist) : 로그인 필요 ----------

export interface WatchlistItem {
  company_id: number
  ticker: string
  company_name: string
  added_at: string
  // 실시간이 아니라 DB에 저장된 최근 종가. 실시간은 RealtimePrice를 따로 호출해야 한다.
  latest_close: number | null
  latest_close_date: string | null
  change_pct: number | null
}

// ---------- 모의투자(/api/portfolio) : 로그인 필요 ----------

export interface Holding {
  ticker: string
  company_name: string
  quantity: number
  avg_price: number
  current_price: number | null
  is_realtime: boolean
  eval_amount: number | null
  profit_loss: number | null
  profit_loss_pct: number | null
}

export interface Portfolio {
  cash_balance: number
  holdings: Holding[]
  total_eval_amount: number
  total_asset: number
  note: string | null
}

export interface OrderResult {
  ticker: string
  company_name: string
  side: 'buy' | 'sell'
  quantity: number
  price: number
  executed_at: string
  cash_balance: number
  holding: { quantity: number; avg_price: number } | null // 매도로 전량 청산되면 null
}

export interface Trade {
  id: number
  ticker: string
  company_name: string
  side: 'buy' | 'sell'
  quantity: number
  price: number
  amount: number // price * quantity, 서버가 계산
  executed_at: string
}

export interface TradeList {
  total: number
  limit: number
  offset: number
  items: Trade[]
}

// ---------- 포트폴리오 AI 진단(/api/portfolio/diagnosis) : 로그인 필요 ----------

export interface DiagnosisHolding {
  ticker: string
  company_name: string
  market: string
  quantity: number
  avg_price: number
  current_price: number | null
  is_realtime: boolean
  price_unavailable: boolean
  eval_amount: number | null
  weight_pct: number | null
  profit_loss: number | null
  profit_loss_pct: number | null
  return_20d_pct: number | null
}

export interface HoldingBrief {
  ticker: string
  company_name: string
  profit_loss_pct: number | null
}

export interface DiagnosisMetrics {
  total_asset: number
  cash_balance: number
  cash_weight_pct: number | null
  stock_eval_amount: number
  holding_count: number
  priced_holding_count: number
  top1_weight_pct: number | null
  top3_weight_pct: number | null
  herfindahl_index: number | null
  market_weights_pct: Record<string, number | null>
  total_profit_loss: number
  total_profit_loss_pct: number | null
  best_holding: HoldingBrief | null
  worst_holding: HoldingBrief | null
}

export interface DiagnosisFlag {
  code: string
  message: string
  value: number
  threshold: number
  ticker: string | null
  company_name: string | null
  market: string | null
}

export interface DiagnosisResponse {
  generated_at: string
  source: 'llm' | 'rule_based'
  model: string | null
  metrics: DiagnosisMetrics
  holdings: DiagnosisHolding[]
  flags: DiagnosisFlag[]
  summary: string
  strengths: string[]
  risks: string[]
  suggestions: string[]
  notes: string[]
  disclaimer: string
}
