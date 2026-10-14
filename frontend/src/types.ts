// backend/API.md 기준 타입. 필드 이름은 백엔드 응답 그대로 따른다.

// ---------- 종목 유형(2026-10-14 추가) ----------

export type AssetType = 'stock' | 'etf'

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
  // ETF는 market/sector가 null
  market: string | null
  sector: string | null
  asset_type: AssetType
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
  // 요청한 유형을 그대로 돌려준다(기본 stock). 주식/ETF는 최신 거래일이 다를 수 있어 따로 집계된다.
  asset_type: AssetType
  price_date: string
  window_days: number
  items: TrendingItem[]
}

// ---------- 관심종목(/api/watchlist) : 로그인 필요 ----------

export interface WatchlistItem {
  company_id: number
  ticker: string
  company_name: string
  asset_type: AssetType
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
  asset_type: AssetType
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
  asset_type: AssetType
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
  asset_type: AssetType
  // ETF는 "ETF"(2026-10-14 전에는 "OTHER")
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
  // 2026-10-14 추가: 이 진단이 이력에 저장된 id/시각. 저장 실패 시(DB 오류 등, 드문 경우) 둘 다 null -
  // 그래도 응답 자체는 200이고 나머지 필드는 정상이다.
  diagnosis_id: number | null
  created_at: string | null
}

// ---------- 포트폴리오 진단 이력(/api/portfolio/diagnosis/history 등, 2026-10-14 추가) : 로그인 필요 ----------

// GET /api/portfolio/diagnosis/history의 items[], /compare의 current/previous와 같은 모양(요약).
export interface DiagnosisFlagSummary {
  code: string
  ticker: string | null
  company_name: string | null
  market: string | null
}

export interface DiagnosisHistoryItem {
  id: number
  created_at: string
  source: 'llm' | 'rule_based'
  total_asset: number
  holdings_count: number
  flags: DiagnosisFlagSummary[]
  // summary 앞 80자(80자 이하면 전체). 잘렸다는 표시는 없다.
  summary_preview: string
}

export interface DiagnosisHistoryList {
  total: number
  limit: number
  offset: number
  items: DiagnosisHistoryItem[]
}

// GET /api/portfolio/diagnosis/{id} - 저장된 진단 응답 그대로(diagnosis_id 대신 id) + created_at.
// POST 응답과 같은 코드로 그릴 수 있도록 diagnosis_id만 id로 바뀐 모양이다.
export type DiagnosisDetail = Omit<DiagnosisResponse, 'diagnosis_id'> & { id: number }

export interface DiagnosisTotalAssetChange {
  previous: number
  current: number
  change_amount: number
  // 이전 총자산이 0이면 null
  change_pct: number | null
}

export interface DiagnosisHoldingChange {
  ticker: string
  company_name: string
  status: 'added' | 'removed' | 'kept'
  previous_weight_pct: number | null
  current_weight_pct: number | null
  weight_change_pp: number | null
}

export interface DiagnosisReturnChange {
  ticker: string
  company_name: string
  previous_return_20d_pct: number | null
  current_return_20d_pct: number | null
  change_pp: number | null
}

export interface DiagnosisChanges {
  total_asset: DiagnosisTotalAssetChange
  // 두 진단에 나온 모든 종목이 한 번씩, 비중 변화 폭이 큰 순
  holdings: DiagnosisHoldingChange[]
  // 같은 플래그인지는 (code, ticker, market)으로 가른다
  flags: { added: DiagnosisFlag[]; resolved: DiagnosisFlag[] }
  // 양쪽 진단에 모두 있고 둘 다 null이 아닌 종목만
  returns_20d: DiagnosisReturnChange[]
}

// GET /api/portfolio/diagnosis/{id}/compare
export interface DiagnosisCompareResponse {
  current: DiagnosisHistoryItem
  // 첫 진단이거나 이전 진단이 이미 삭제/정리됐으면 previous/changes가 null(에러 아님, 200)
  previous: DiagnosisHistoryItem | null
  changes: DiagnosisChanges | null
  disclaimer: string
}
