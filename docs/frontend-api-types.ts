// 자동 생성 파일: backend/scripts/export_api_types.py가 FastAPI OpenAPI 스키마에서 만든다. 손으로 고치지 말 것.
// 다시 만들기: cd backend && python -m scripts.export_api_types
// 오류 응답은 422만 {detail: ValidationErrorItem[]}이고 나머지(400·401·404·409·413·429·500·503)는 ErrorResponse {detail: string}다.
// 날짜는 "YYYY-MM-DD" 문자열, 시각(*_at)은 오프셋이 붙은 ISO 8601 문자열(UTC "+00:00" 또는 KST "+09:00")이다.

export interface AccountDeleteResponse {
  ok?: boolean;
  status: string;
  message: string;
}

export interface AuthResponse {
  user: UserOut;
  access_token: string;
  token_type?: string;
}

export interface BacktestRequest {
  tickers: string[];
  strategy: "buy_and_hold" | "sma_cross" | "dca";
  params?: Record<string, unknown>;
  start: string;
  end: string;
  initial_cash?: number;
  fee_rate?: number;
  sell_tax_rate?: number;
}

export interface BacktestResponse {
  generated_at: string;
  input: InputOut;
  period: PeriodOut;
  metrics: Metrics;
  per_ticker: PerTicker[];
  benchmark: BenchmarkOut | null;
  curve: app__api__backtest__Curve;
  trades: Trades;
  assumptions: string[];
  data_warnings: Warning_[];
  notices: string[];
  disclaimer: string;
}

export interface Benchmark {
  index_code: string;
  index_name: string;
  start_date: string;
  end_date: string;
  index_return_pct: number | null;
  portfolio_return_pct: number | null;
  excess_return_pct: number | null;
  covers_full_period: boolean;
}

export interface BenchmarkOut {
  index_code: string;
  index_name: string;
  start_date: string;
  end_date: string;
  return_pct: number | null;
  excess_return_pct: number | null;
  covers_full_period: boolean;
}

export interface BriefingHistory {
  items: app__api__daily_briefing__BriefingItem[];
  page: number;
  size: number;
  total: number;
  has_next: boolean;
  total_pages: number;
  timezone: string;
  latest_only: boolean;
  date_from: string | null;
  date_to: string | null;
}

export interface BriefingRecord {
  id: number;
  version: number;
  local_date: string;
  timezone: string;
  locale: string;
  generated_at: string;
  data_as_of: string | null;
  market_data_status: "latest" | "delayed" | "stale" | "no_data";
  summary_source: "llm" | "code";
  section_status: Record<string, string>;
  tickers: Record<string, unknown>[];
  sources: Record<string, unknown>[];
  content: Record<string, unknown>;
  original_content: Record<string, unknown> | null;
  date_group: "today" | "yesterday" | "last_7_days" | "last_30_days" | "older";
  created?: boolean | null;
}

export interface BriefingResponse {
  generated_at: string;
  data_date: string | null;
  stale: boolean;
  stale_notice: string | null;
  message: string | null;
  market: Market;
  portfolio: Portfolio;
  holdings: HoldingBriefingItem[];
  watchlist: app__api__briefing__BriefingItem[];
  highlights: app__api__briefing__Flag[];
  disclaimer: string;
}

/** PUT /api/auth/email(2026-10-30 보강): 현재 비밀번호가 필수다(처음 등록할 때도). 탈취한 토큰만으로 복구 주소를 바꾸지 못하게. */
export interface ChangeEmailRequest {
  email: string;
  current_password: string;
}

export interface ChangePasswordRequest {
  current_password: string;
  new_password: string;
}

export interface CompanyExposure {
  rank: number;
  name: string;
  code: string | null;
  linked: boolean;
  direct_amount: number;
  via_etf_amount: number;
  via_etfs: ViaEtf[];
  total_amount: number;
  effective_weight_pct: number;
  direct_weight_pct: number;
}

export interface CompanyItem {
  ticker: string;
  name: string;
  market?: string | null;
  sector?: string | null;
  asset_type?: "stock" | "etf";
  latest_close?: number | null;
  change_pct?: number | null;
}

export interface ComparisonBasis {
  current_created_at: string;
  current_generated_at?: string | null;
  previous_created_at?: string | null;
  previous_generated_at?: string | null;
  recomputed: boolean;
  note?: string | null;
}

export interface Contribution {
  ticker: string;
  company_name: string;
  profit_loss: number;
  contribution_pct: number | null;
  quantity: number;
  eval_amount: number;
  unrealized_profit_loss: number | null;
  realized_profit_loss: number;
}

export interface CurvePoint {
  date: string;
  total_asset: number;
  cash_balance: number;
  eval_amount: number;
  return_pct: number | null;
}

/** 당시 tool 결과에 남은 기준일. 키는 결과에 있던 것만 채워진다(나머지는 null). */
export interface DataBasis {
  tool: string;
  company_name?: string | null;
  ticker?: string | null;
  period_start_date?: string | null;
  period_end_date?: string | null;
  base_date?: string | null;
  latest_date?: string | null;
  data_date?: string | null;
  price_as_of?: string | null;
  as_of?: string | null;
}

export interface DataGap {
  ticker: string;
  company_name: string;
  kind: "previous_close" | "trade_price";
  days: number;
  first_date: string;
  last_date: string;
}

export interface DataWarning {
  kind: string;
  message: string;
}

export interface DecomposedEtf {
  ticker: string;
  name: string;
  eval_amount: number;
  weight_pct: number;
  holdings_as_of: string | null;
  stale: boolean;
  stale_notice: string | null;
  constituent_count: number;
  weight_sum_pct: number;
}

/** DELETE /api/auth/me 본문. 비밀번호가 있는 계정은 current_password가 필수다(없으면 422). */
export interface DeleteAccountRequest {
  current_password?: string | null;
  confirm_username?: string | null;
}

export interface DiagnosisChanges {
  total_asset: TotalAssetChange;
  holdings: HoldingWeightChange[];
  flags: FlagChanges;
  returns_20d: ReturnChange[];
}

export interface DiagnosisComparison {
  current: DiagnosisHistoryItem;
  previous?: DiagnosisHistoryItem | null;
  changes?: DiagnosisChanges | null;
  disclaimer?: string;
}

export interface DiagnosisDetail {
  generated_at: string;
  source: "llm" | "rule_based";
  model?: string | null;
  metrics: DiagnosisMetrics;
  holdings: DiagnosisHolding[];
  flags: DiagnosisFlag[];
  summary: string;
  strengths: string[];
  risks: string[];
  suggestions: string[];
  notes: string[];
  disclaimer: string;
  look_through?: Record<string, unknown> | null;
  id: number;
  created_at: string;
}

export interface DiagnosisFlag {
  code: string;
  message: string;
  value: number;
  threshold: number;
  ticker?: string | null;
  company_name?: string | null;
  market?: string | null;
}

export interface DiagnosisFlagBrief {
  code: string;
  ticker?: string | null;
  company_name?: string | null;
  market?: string | null;
}

export interface DiagnosisHistoryItem {
  id: number;
  created_at: string;
  source: "llm" | "rule_based";
  total_asset: number;
  holdings_count: number;
  flags: DiagnosisFlagBrief[];
  summary_preview: string;
}

export interface DiagnosisHistoryList {
  total: number;
  limit: number;
  offset: number;
  has_next: boolean;
  total_pages: number;
  items: DiagnosisHistoryItem[];
}

export interface DiagnosisHistoryRecordItem {
  id: number;
  created_at: string;
  source: "llm" | "rule_based";
  total_asset: number;
  holdings_count: number;
  flags: DiagnosisFlagBrief[];
  summary_preview: string;
  title: string;
  locale: string;
  cash_weight_pct?: number | null;
  top_holdings: TopHolding[];
  risk_summary?: string | null;
  flag_codes: string[];
  generated_at?: string | null;
  local_date: string;
  local_time: string;
  date_group: "today" | "yesterday" | "last_7_days" | "last_30_days" | "older";
}

export interface DiagnosisHistoryRecordList {
  total: number;
  limit: number;
  offset: number;
  has_next: boolean;
  total_pages: number;
  timezone: string;
  items: DiagnosisHistoryRecordItem[];
}

export interface DiagnosisHolding {
  ticker: string;
  company_name: string;
  market: string;
  asset_type?: "stock" | "etf";
  quantity: number;
  avg_price: number;
  current_price?: number | null;
  is_realtime: boolean;
  price_unavailable: boolean;
  eval_amount?: number | null;
  weight_pct?: number | null;
  profit_loss?: number | null;
  profit_loss_pct?: number | null;
  return_20d_pct?: number | null;
}

export interface DiagnosisMetrics {
  total_asset: number;
  cash_balance: number;
  cash_weight_pct?: number | null;
  stock_eval_amount: number;
  holding_count: number;
  priced_holding_count: number;
  top1_weight_pct?: number | null;
  top3_weight_pct?: number | null;
  herfindahl_index?: number | null;
  market_weights_pct: Record<string, number | null>;
  total_profit_loss: number;
  total_profit_loss_pct?: number | null;
  best_holding?: HoldingBrief | null;
  worst_holding?: HoldingBrief | null;
}

export interface DiagnosisPart {
  diagnosis_id: number;
  source: "llm" | "rule_based";
  total_asset: number;
  holdings_count: number;
  cash_weight_pct?: number | null;
  top_holdings: TopHolding[];
  risk_summary?: string | null;
  flags: DiagnosisFlagBrief[];
  flag_codes: string[];
  generated_at?: string | null;
}

export interface DiagnosisRecordComparison {
  current: DiagnosisHistoryItem;
  previous?: DiagnosisHistoryItem | null;
  changes?: DiagnosisChanges | null;
  disclaimer?: string;
  basis: ComparisonBasis;
  metric_changes?: MetricChanges | null;
  exposure_changes?: Record<string, unknown> | null;
}

export interface DiagnosisResponse {
  generated_at: string;
  source: "llm" | "rule_based";
  model?: string | null;
  metrics: DiagnosisMetrics;
  holdings: DiagnosisHolding[];
  flags: DiagnosisFlag[];
  summary: string;
  strengths: string[];
  risks: string[];
  suggestions: string[];
  notes: string[];
  disclaimer: string;
  look_through?: Record<string, unknown> | null;
  diagnosis_id?: number | null;
  created_at?: string | null;
}

export interface DisclaimerResponse {
  text: string;
}

export interface DisclosureItem {
  title: string;
  disclosure_type: string | null;
  disclosed_at: string;
  source_url: string | null;
}

export interface Drawdown {
  pct: number;
  peak_date: string | null;
  trough_date: string | null;
  recovery_date: string | null;
  duration_days: number;
}

export interface EmailRequest {
  email: string;
}

export interface EmailStatus {
  email: string | null;
  email_verified: boolean;
  verification_sent: boolean;
  message: string;
}

export interface ErrorResponse {
  detail: string;
}

export interface EventReaction {
  event_type: "disclosure" | "news";
  title: string;
  url: string | null;
  event_at: string | null;
  event_date: string | null;
  time_known: boolean;
  base_date: string | null;
  base_close: number | null;
  reaction_start_date: string | null;
  returns: Returns;
  volume_ratio: number | null;
  volume_sample_days: number;
  other_events_in_window: number;
  note: string | null;
}

export interface EventReactionsResponse {
  ticker: string;
  company_name: string;
  asset_type: string;
  price_as_of: string | null;
  market_index: string | null;
  events: EventReaction[];
  summary: Record<string, number | null>;
  disclaimer: string;
}

export interface ExchangeRequest {
  code: string;
}

export interface ExposureResponse {
  generated_at: string;
  valuation_date: string | null;
  stale: boolean;
  stale_notice: string | null;
  etf_holdings_stale: boolean;
  message: string | null;
  limit: number;
  summary: app__api__exposure__Summary;
  companies: CompanyExposure[];
  overlaps: CompanyExposure[];
  decomposed_etfs: DecomposedEtf[];
  undecomposed_etfs: UndecomposedEtf[];
  flags: app__api__exposure__Flag[];
  data_warnings: DataWarning[];
  disclaimer: string;
}

export interface FlagChanges {
  added: DiagnosisFlag[];
  resolved: DiagnosisFlag[];
}

export interface GenerateRequest {
  regenerate?: boolean;
  locale?: string | null;
  timezone?: string | null;
  use_llm?: boolean;
}

export interface HTTPValidationError {
  detail?: ValidationError[];
}

export interface HealthResponse {
  status: string;
  checks: Record<string, Record<string, unknown>>;
}

export interface HistoryCounts {
  all: number;
  research: number;
  diagnosis: number;
}

export interface HistoryItem {
  kind: "research" | "diagnosis";
  id: number;
  created_at: string;
  local_date: string;
  local_time: string;
  date_group: "today" | "yesterday" | "last_7_days" | "last_30_days" | "older";
  title: string;
  summary?: string | null;
  locale: string;
  company_names: string[];
  tickers: string[];
  detail_path: string;
  research?: ResearchPart | null;
  diagnosis?: DiagnosisPart | null;
}

export interface HistorySearchResponse {
  type: "all" | "research" | "diagnosis";
  keyword?: string | null;
  terms: string[];
  date_from?: string | null;
  date_to?: string | null;
  timezone: string;
  sort: string;
  page: number;
  size: number;
  total: number;
  total_pages: number;
  has_next: boolean;
  counts: HistoryCounts;
  items: HistoryItem[];
}

export interface HoldingAfterOrder {
  quantity: number;
  avg_price: number;
}

export interface HoldingBrief {
  ticker: string;
  company_name: string;
  profit_loss_pct?: number | null;
}

export interface HoldingBriefingItem {
  ticker: string;
  name: string;
  asset_type: "stock" | "etf";
  data_available: boolean;
  latest_close: number | null;
  latest_date: string | null;
  day_change_pct: number | null;
  week_change_pct: number | null;
  volume: number | null;
  avg_volume_20d: number | null;
  volume_ratio: number | null;
  news: NewsItem[];
  disclosures: DisclosureItem[];
  disclosure_count: number;
  notice: string | null;
  flags: app__api__briefing__Flag[];
  quantity: number;
  avg_price: number;
  eval_amount: number | null;
  weight_pct: number | null;
  profit_loss: number | null;
  profit_loss_pct: number | null;
  day_profit_loss: number | null;
}

export interface HoldingWeightChange {
  ticker: string;
  company_name: string;
  status: "added" | "removed" | "kept";
  previous_weight_pct?: number | null;
  current_weight_pct?: number | null;
  weight_change_pp?: number | null;
}

export interface HoldingsResponse {
  ticker: string;
  name: string;
  asset_type: string;
  as_of_date: string | null;
  collected_at: string | null;
  stale: boolean;
  stale_notice: string | null;
  source: string | null;
  holding_count: number;
  total_weight_pct: number | null;
  top_weight_pct: number | null;
  limit: number;
  items: app__api__etf__HoldingItem[];
  data_warnings: DataWarning[];
  message: string | null;
  disclaimer: string;
}

export interface Horizon {
  pct: number | null;
  index_pct: number | null;
  excess_pct: number | null;
  available: boolean;
  reason: string | null;
}

export interface InputOut {
  tickers: TickerOut[];
  strategy: string;
  params: Record<string, number>;
  start: string;
  end: string;
  initial_cash: number;
  fee_rate: number;
  sell_tax_rate: number;
}

export interface LastRefresh {
  finished_at: string | null;
  ok: boolean;
  failed_steps: string[];
}

export interface LatestDate {
  latest_date: string | null;
}

export interface LoginRequest {
  username: string;
  password: string;
}

export interface LogoutResponse {
  ok?: boolean;
}

/** view=look_through(2026-11-06, app/look_through). 비중의 분모는 총자산(현금 + 주식 + ETF). 필드 설명은 API.md. */
export interface LookThroughResponse {
  generated_at: string;
  calc_version: string;
  locale: string;
  status: "ok" | "empty" | "no_prices";
  message: string | null;
  has_account: boolean;
  valuation_date: string | null;
  expected_date: string;
  price_lag_business_days: number | null;
  stale: boolean;
  stale_notice: string | null;
  etf_holdings_stale: boolean;
  limit: number;
  summary: LookThroughSummary;
  companies: Record<string, unknown>[];
  overlaps: Record<string, unknown>[];
  etfs: Record<string, unknown>[];
  unpriced: Record<string, unknown>[];
  sector: Record<string, unknown>;
  flags: Record<string, unknown>[];
  data_warnings: Record<string, unknown>[];
  disclaimer: string;
}

export interface LookThroughSummary {
  cash_balance: number;
  holdings_value: number;
  total_asset: number;
  stock_amount: number;
  etf_amount: number;
  direct_amount: number;
  via_etf_amount: number;
  companies_amount: number;
  linked_via_amount: number;
  unlinked_via_amount: number;
  nested_etf_amount: number;
  residual_amount: number;
  undecomposed_etf_amount: number;
  unidentified_amount: number;
  unidentified_weight_pct: number | null;
  over_allocated_amount: number;
  cash_weight_pct: number | null;
  companies_weight_pct: number | null;
  holding_count: number;
  priced_holding_count: number;
  unpriced_count: number;
  company_count: number;
  direct_top1_weight_pct: number | null;
  look_through_top1: Record<string, unknown> | null;
}

export interface Market {
  kospi: Quote | null;
  kosdaq: Quote | null;
  usd_krw: Quote | null;
}

export interface MarketIndexStatus {
  latest_date: string | null;
  stale: boolean;
}

export interface MaxDrawdown {
  pct: number;
  peak_date: string | null;
  trough_date: string | null;
  recovery_date: string | null;
  duration_days: number;
}

/** GET /api/auth/me: 기존 id·username에 이메일 상태만 더한다(본인에게는 마스킹하지 않는다). 가입·로그인 응답의 user는 그대로다. */
export interface MeOut {
  id: number;
  username: string;
  email: string | null;
  email_verified: boolean;
}

export interface MessageResponse {
  ok?: boolean;
  message: string;
}

export interface MetricChange {
  previous?: number | null;
  current?: number | null;
  change?: number | null;
}

export interface MetricChanges {
  cash_balance: MetricChange;
  cash_weight_pct: MetricChange;
  stock_eval_amount: MetricChange;
  holding_count: MetricChange;
  top1_weight_pct: MetricChange;
  top3_weight_pct: MetricChange;
  herfindahl_index: MetricChange;
  total_profit_loss_pct: MetricChange;
}

export interface Metrics {
  final_value: number;
  total_return_pct: number | null;
  cagr_pct: number | null;
  max_drawdown: Drawdown;
  annualized_volatility_pct: number | null;
  sharpe_like: number | null;
  trade_count: number;
  buy_count: number;
  sell_count: number;
  fees_paid: number;
  taxes_paid: number;
  ending_cash: number;
}

export interface NewsItem {
  title: string;
  source: string | null;
  published_at: string;
  url: string | null;
}

export interface NewsStatus {
  latest_published_date: string | null;
}

export interface OrderRequest {
  ticker: string;
  side: "buy" | "sell";
  quantity: number;
}

export interface OrderResponse {
  ticker: string;
  company_name: string;
  side: "buy" | "sell";
  quantity: number;
  price: number;
  executed_at: string;
  cash_balance: number;
  holding?: HoldingAfterOrder | null;
}

export interface PerTicker {
  ticker: string;
  name: string;
  start_value: number;
  final_value: number;
  return_pct: number | null;
  quantity: number;
  trade_count: number;
}

export interface PerformanceResponse {
  generated_at: string;
  period: "1m" | "3m" | "6m" | "1y" | "all";
  data_date: string | null;
  stale: boolean;
  stale_notice: string | null;
  start_date: string | null;
  end_date: string | null;
  initial_balance: number;
  message: string | null;
  summary: app__api__performance__Summary;
  risk: Risk;
  trades: TradeStats;
  contributions: Contribution[];
  benchmark: Benchmark | null;
  curve: app__api__performance__Curve;
  data_gaps: DataGap[];
  notices: string[];
  disclaimer: string;
}

/** view=benchmark(2026-11-06, app.performance_compare). 필드 설명은 API.md "성과·벤치마크 비교" 절. */
export interface PerformanceViewResponse {
  generated_at: string;
  calc_version: string;
  locale: string;
  status: "ok" | "partial" | "no_account" | "no_trades" | "pending_only" | "no_prices" | "history_unavailable";
  message: string | null;
  history_verified: boolean;
  period: "1m" | "3m" | "6m" | "1y" | "all";
  requested_start_date: string | null;
  start_date: string | null;
  end_date: string | null;
  data_date: string | null;
  expected_date: string;
  data_lag_business_days: number | null;
  initial_balance: number;
  benchmark_code: "KS11" | "KQ11";
  benchmark_name: string;
  summary: Record<string, unknown>;
  metrics: Record<string, unknown>;
  series: Record<string, unknown>;
  comparison: Record<string, unknown>;
  estimated_dates: string[];
  cash_flows: Record<string, unknown>;
  price_basis: Record<string, unknown>;
  warnings: Record<string, unknown>[];
  disclaimer: string;
}

export interface PeriodOut {
  requested_start: string;
  requested_end: string;
  start: string;
  end: string;
  trading_days: number;
  adjusted: boolean;
}

export interface Point {
  date: string;
  total_value: number;
  cash: number;
}

export interface Portfolio {
  has_account: boolean;
  initial_balance: number;
  cash_balance: number | null;
  holdings_value: number | null;
  total_asset: number | null;
  profit_loss: number | null;
  profit_loss_pct: number | null;
  day_profit_loss: number | null;
  top_weight: WeightItem | null;
  weights: WeightItem[];
  message: string | null;
}

export interface PortfolioResponse {
  cash_balance: number;
  holdings: app__api__portfolio__HoldingItem[];
  total_eval_amount: number;
  total_asset: number;
  note?: string | null;
}

/** 둘 다 선택. 보내지 않은 필드는 그대로다. preferred_timezone에 null을 보내면 기본값(Asia/Seoul)으로 되돌린다. */
export interface PreferencesIn {
  preferred_locale?: string | null;
  preferred_timezone?: string | null;
}

/** GET/PUT /api/auth/preferences. 기존 계정은 preferred_locale=ko-KR, preferred_timezone=null(= Asia/Seoul)이다. */
export interface PreferencesOut {
  preferred_locale: string;
  preferred_timezone: string | null;
  effective_timezone: string;
  supported_locales: string[];
  default_locale: string;
}

export interface PriceHistoryItem {
  date: string;
  close: number | null;
  volume: number | null;
  change_pct: number | null;
  open: number | null;
  high: number | null;
  low: number | null;
}

export interface PriceHistoryResponse {
  ticker: string;
  company_name: string;
  asset_type: "stock" | "etf";
  days: number;
  count: number;
  price_as_of: string | null;
  items: PriceHistoryItem[];
}

export interface ProviderOut {
  id: string;
  name: string;
  login_path: string;
}

export interface ProvidersResponse {
  providers: ProviderOut[];
}

export interface Quote {
  code: string;
  name: string;
  date: string;
  close: number;
  change_pct: number | null;
}

export interface RealtimePriceResponse {
  ticker: string;
  company_name: string;
  current_price: number;
  change_amount?: number | null;
  change_pct?: number | null;
  as_of: string;
  queried_at: string;
  is_realtime: boolean;
  source: "naver" | "fallback";
  corrected_from?: string | null;
}

export interface ReportDetail {
  report_id: number;
  previous_report_id?: number | null;
  question: string;
  answer: string;
  summary?: string | null;
  company_name?: string | null;
  created_at: string;
  used_tools: string[];
  sources: Source[];
  disclaimer?: string;
}

export interface ReportList {
  total: number;
  has_next: boolean;
  total_pages: number;
  items: ReportListItem[];
}

export interface ReportListItem {
  report_id: number;
  previous_report_id?: number | null;
  question: string;
  summary?: string | null;
  company_name?: string | null;
  created_at: string;
  used_tools: string[];
}

export interface ReportRecordDetail {
  report_id: number;
  previous_report_id?: number | null;
  question: string;
  answer: string;
  summary?: string | null;
  company_name?: string | null;
  created_at: string;
  used_tools: string[];
  sources: Source[];
  disclaimer?: string;
  title: string;
  company_names: string[];
  tickers: string[];
  analysis_type: "outlook" | "event" | "briefing" | "discovery" | "etf" | "disclosure" | "news" | "price" | "market" | "general";
  analysis_types: ("outlook" | "event" | "briefing" | "discovery" | "etf" | "disclosure" | "news" | "price" | "market" | "general")[];
  source_count: number;
  local_date: string;
  local_time: string;
  date_group: "today" | "yesterday" | "last_7_days" | "last_30_days" | "older";
  data_basis: DataBasis[];
  timezone: string;
  locale: string;
  translation_status?: string | null;
  original_answer?: string | null;
}

export interface ReportRecordItem {
  report_id: number;
  previous_report_id?: number | null;
  question: string;
  summary?: string | null;
  company_name?: string | null;
  created_at: string;
  used_tools: string[];
  title: string;
  company_names: string[];
  tickers: string[];
  analysis_type: "outlook" | "event" | "briefing" | "discovery" | "etf" | "disclosure" | "news" | "price" | "market" | "general";
  analysis_types: ("outlook" | "event" | "briefing" | "discovery" | "etf" | "disclosure" | "news" | "price" | "market" | "general")[];
  source_count: number;
  local_date: string;
  local_time: string;
  date_group: "today" | "yesterday" | "last_7_days" | "last_30_days" | "older";
  locale: string;
}

export interface ReportRecordList {
  total: number;
  has_next: boolean;
  total_pages: number;
  timezone: string;
  items: ReportRecordItem[];
}

export interface ResearchPart {
  report_id: number;
  previous_report_id?: number | null;
  question: string;
  analysis_type: "outlook" | "event" | "briefing" | "discovery" | "etf" | "disclosure" | "news" | "price" | "market" | "general";
  analysis_types: ("outlook" | "event" | "briefing" | "discovery" | "etf" | "disclosure" | "news" | "price" | "market" | "general")[];
  source_count: number;
  used_tools: string[];
}

export interface ResearchRequest {
  question: string;
  previous_report_id?: number | null;
  locale?: string | null;
}

export interface ResearchResponse {
  answer: string;
  used_tools: string[];
  sources?: Source[];
  report_id?: number | null;
  previous_report_id?: number | null;
  disclaimer?: string;
  locale?: string;
  translation_status?: "translated" | "localized" | "fallback_summary" | null;
}

export interface ResetPasswordRequest {
  token: string;
  new_password: string;
}

export interface ReturnChange {
  ticker: string;
  company_name: string;
  previous_return_20d_pct: number;
  current_return_20d_pct: number;
  change_pp: number;
}

export interface Returns {
  d1: Horizon;
  d5: Horizon;
  d20: Horizon;
}

export interface Risk {
  max_drawdown: MaxDrawdown;
  daily_volatility_pct: number | null;
  annualized_volatility_pct: number | null;
  daily_return_count: number;
}

export interface SignupRequest {
  username: string;
  password: string;
  email?: string | null;
}

export interface Source {
  tool: string;
  type: "news" | "disclosure";
  title: string;
  company_names: string[];
  company_filter?: string | null;
  url?: string | null;
}

export interface Sources {
  stock_price: LatestDate;
  etf_price: LatestDate;
  market_index: MarketIndexStatus;
  exchange_rate: LatestDate;
  news: NewsStatus;
  disclosure: LatestDate;
}

export interface StatusResponse {
  generated_at: string;
  sources: Sources;
  last_refresh: LastRefresh | null;
  is_realtime: boolean;
  note: string;
}

export interface TickerOut {
  ticker: string;
  name: string;
}

export interface TokenRequest {
  token: string;
}

export interface TopHolding {
  ticker?: string | null;
  company_name?: string | null;
  weight_pct?: number | null;
}

export interface TotalAssetChange {
  previous: number;
  current: number;
  change_amount: number;
  change_pct?: number | null;
}

export interface TradeItem {
  id: number;
  ticker: string;
  company_name: string;
  asset_type?: "stock" | "etf";
  side: "buy" | "sell";
  quantity: number;
  price: number;
  amount: number;
  executed_at: string;
}

export interface TradeList {
  total: number;
  limit: number;
  offset: number;
  has_next: boolean;
  total_pages: number;
  items: TradeItem[];
}

export interface TradeOut {
  ticker: string;
  side: "buy" | "sell";
  signal_date: string;
  date: string;
  quantity: number;
  price: number;
  amount: number;
  fee: number;
  tax: number;
  cash_after: number;
}

export interface TradeStats {
  count: number;
  buys: number;
  sells: number;
  wins: number;
  losses: number;
  even: number;
  win_rate_pct: number | null;
  realized_profit_loss: number;
  pending_count: number;
}

export interface Trades {
  items: TradeOut[];
  total: number;
  truncated: boolean;
  max_items: number;
}

export interface TrendingItem {
  ticker: string;
  company_name: string;
  close_price: number;
  change_pct?: number | null;
  volume?: number | null;
  avg_volume?: number | null;
  volume_ratio?: number | null;
}

export interface TrendingResponse {
  category: "gainers" | "losers" | "volume_surge";
  asset_type?: "stock" | "etf";
  price_date: string;
  window_days: number;
  items: TrendingItem[];
}

export interface UndecomposedEtf {
  ticker: string;
  name: string;
  eval_amount: number;
  weight_pct: number;
  reason_code: string;
  reason: string;
}

export interface UserOut {
  id: number;
  username: string;
}

export interface ValidationError {
  loc: (string | number)[];
  msg: string;
  type: string;
  input?: unknown;
  ctx?: Record<string, unknown>;
}

export interface ViaEtf {
  ticker: string;
  name: string;
  amount: number;
  etf_weight_pct: number;
}

export interface Warning_ {
  ticker: string;
  name: string;
  kind: "jump" | "non_positive" | "flat_run";
  date: string;
  detail: string;
  value: number | null;
}

export interface WatchlistCreate {
  ticker: string;
}

export interface WatchlistItem {
  company_id: number;
  ticker: string;
  company_name: string;
  asset_type?: "stock" | "etf";
  added_at: string;
  latest_close?: number | null;
  latest_close_date?: string | null;
  change_pct?: number | null;
}

export interface WatchlistList {
  items: WatchlistItem[];
}

export interface WeightItem {
  ticker: string;
  name: string;
  weight_pct: number;
}

export interface app__api__backtest__Curve {
  points: Point[];
  total_points: number;
  sampled: boolean;
  max_points: number;
}

export interface app__api__briefing__BriefingItem {
  ticker: string;
  name: string;
  asset_type: "stock" | "etf";
  data_available: boolean;
  latest_close: number | null;
  latest_date: string | null;
  day_change_pct: number | null;
  week_change_pct: number | null;
  volume: number | null;
  avg_volume_20d: number | null;
  volume_ratio: number | null;
  news: NewsItem[];
  disclosures: DisclosureItem[];
  disclosure_count: number;
  notice: string | null;
  flags: app__api__briefing__Flag[];
}

export interface app__api__briefing__Flag {
  type: "concentration" | "day_move" | "week_move" | "volume_spike" | "new_disclosure";
  scope: "holding" | "watchlist";
  ticker: string;
  name: string;
  text: string;
  value: number;
  threshold: number;
  unit: "%" | "배" | "건";
  date: string | null;
  direction: "up" | "down" | null;
}

export interface app__api__daily_briefing__BriefingItem {
  id: number;
  version: number;
  versions_on_date: number;
  local_date: string;
  local_time: string;
  date_group: "today" | "yesterday" | "last_7_days" | "last_30_days" | "older";
  generated_at: string;
  locale: string;
  data_as_of: string | null;
  market_data_status: "latest" | "delayed" | "stale" | "no_data";
  summary_source: "llm" | "code";
  title: string | null;
  headline: string | null;
  summary: string | null;
  section_status: Record<string, string>;
  ticker_count: number;
  detail_path: string;
}

export interface app__api__etf__HoldingItem {
  rank: number;
  name: string;
  code: string | null;
  weight_pct: number;
}

export interface app__api__exposure__Flag {
  kind: string;
  name: string;
  code: string | null;
  value_pct: number;
  threshold_pct: number;
  message: string;
}

export interface app__api__exposure__Summary {
  total_eval_amount: number;
  holding_count: number;
  stock_amount: number;
  etf_amount: number;
  decomposed_etf_amount: number;
  undecomposed_etf_amount: number;
  companies_amount: number;
  other_amount: number;
  other_weight_pct: number;
  company_count: number;
}

export interface app__api__performance__Curve {
  points: CurvePoint[];
  total_points: number;
  sampled: boolean;
  max_points: number;
}

export interface app__api__performance__Summary {
  start_value: number;
  end_value: number;
  cash_balance: number;
  eval_amount: number;
  profit_loss: number;
  cumulative_return_pct: number | null;
  period_return_pct: number | null;
  cash_weight_pct: number | null;
}

export interface app__api__portfolio__HoldingItem {
  ticker: string;
  company_name: string;
  asset_type?: "stock" | "etf";
  quantity: number;
  avg_price: number;
  current_price?: number | null;
  is_realtime?: boolean;
  eval_amount?: number | null;
  profit_loss?: number | null;
  profit_loss_pct?: number | null;
}
