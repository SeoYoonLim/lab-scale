import { AUTH_EXPIRED_EVENT, clearToken, getToken } from '../utils/token'
import type {
  AuthResponse,
  Company,
  DiagnosisFlag,
  DiagnosisHolding,
  DiagnosisResponse,
  Holding,
  OrderResult,
  Portfolio,
  RealtimePrice,
  ReportDetail,
  ReportListItem,
  ResearchResponse,
  Source,
  Trade,
  TradeList,
  TrendingCategory,
  TrendingItem,
  TrendingResponse,
  User,
  WatchlistItem,
} from '../types'
import type { Api } from './index'

// 화면 개발용 샘플 데이터. 실제 시세/뉴스/공시/사용자가 아니다.

const DISCLAIMER =
  '이 서비스는 학습·시연용 모의투자와 AI 리서치입니다. 제공되는 분석과 의견은 투자 권유나 자문이 아니며, ' +
  '투자 판단과 그에 따른 손익의 책임은 본인에게 있습니다. AI 응답에는 오류가 있을 수 있습니다.'

interface CompanyMeta {
  ticker: string
  name: string
  market: 'KOSPI' | 'KOSDAQ'
}

// 백엔드 DB의 sector 컬럼은 2026-10-07 기준 실제로 비어 있다(README "구현된 API" 참고) — mock도 null로 맞춘다.
const COMPANY_META: CompanyMeta[] = [
  { ticker: '005930', name: '삼성전자', market: 'KOSPI' },
  { ticker: '000660', name: 'SK하이닉스', market: 'KOSPI' },
  { ticker: '035420', name: 'NAVER', market: 'KOSPI' },
  { ticker: '035720', name: '카카오', market: 'KOSPI' },
  { ticker: '005380', name: '현대차', market: 'KOSPI' },
  { ticker: '373220', name: 'LG에너지솔루션', market: 'KOSPI' },
  { ticker: '247540', name: '에코프로비엠', market: 'KOSDAQ' },
]

const BASE_PRICE: Record<string, number> = {
  '005930': 70000,
  '000660': 180000,
  '035420': 200000,
  '035720': 45000,
  '005380': 230000,
  '373220': 380000,
  '247540': 130000,
}

const DISCLOSURE_TYPES = [
  '분기보고서',
  '주요사항보고서(자기주식취득결정)',
  '주식등의대량보유상황보고서',
  '기업설명회(IR)개최',
  '영업(잠정)실적(공정공시)',
]

const delay = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms))

function notFoundReport(reportId: number): Error {
  return new Error(`report_id=${reportId} 리포트를 찾을 수 없습니다.`)
}

function companyNotFound(input: string): Error {
  return new Error(`'${input}' 종목을 찾지 못했습니다. company 테이블에 등록된 종목명 또는 종목코드를 지정해주세요.`)
}

function unauthorized(): Error {
  return new Error('인증이 필요합니다. 다시 로그인해주세요.')
}

// 같은 종목은 항상 같은 값이 나오도록 시드 기반 난수를 쓴다.
function mulberry32(seed: number) {
  let a = seed
  return () => {
    a |= 0
    a = (a + 0x6d2b79f5) | 0
    let t = Math.imul(a ^ (a >>> 15), 1 | a)
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

function toDateString(d: Date) {
  const y = d.getFullYear()
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  return `${y}-${m}-${day}`
}

interface PriceSeriesPoint {
  price_date: string
  close_price: number
  volume: number | null
  change_pct: number | null
}

const priceCache = new Map<string, PriceSeriesPoint[]>()

// 오늘까지 90거래일(주말 제외) 치 주가를 만든다. 백엔드 대응 API가 없어 이 파일 안에서만 쓰는 내부 모델이다.
function getPriceSeries(ticker: string): PriceSeriesPoint[] {
  const cached = priceCache.get(ticker)
  if (cached) return cached

  const rand = mulberry32(Number(ticker) || 1)
  const dates: string[] = []
  const cursor = new Date()
  while (dates.length < 90) {
    const weekday = cursor.getDay()
    if (weekday !== 0 && weekday !== 6) dates.push(toDateString(cursor))
    cursor.setDate(cursor.getDate() - 1)
  }
  dates.reverse()

  let prevClose = BASE_PRICE[ticker] ?? 50000
  const series = dates.map((price_date) => {
    const move = (rand() - 0.5) * 0.05
    const close = Math.max(100, Math.round((prevClose * (1 + move)) / 100) * 100)
    const change_pct = Math.round(((close - prevClose) / prevClose) * 10000) / 100
    const volume = Math.round(8_000_000 + rand() * 20_000_000)
    prevClose = close
    return { price_date, close_price: close, volume, change_pct }
  })

  priceCache.set(ticker, series)
  return series
}

function findCompanyMeta(input: string): CompanyMeta | undefined {
  const needle = input.trim().toLowerCase()
  return COMPANY_META.find((c) => c.ticker === input || c.name.toLowerCase() === needle)
}

function companyView(meta: CompanyMeta): Company {
  const latest = getPriceSeries(meta.ticker).at(-1)
  return {
    ticker: meta.ticker,
    name: meta.name,
    market: meta.market,
    sector: null,
    latest_close: latest?.close_price ?? null,
    change_pct: latest?.change_pct ?? null,
  }
}

function makeNews(name: string, limit: number): { title: string; url: string }[] {
  return Array.from({ length: limit }, (_, i) => ({
    title: `[샘플] ${name} 관련 뉴스 ${i + 1}`,
    url: `https://search.naver.com/search.naver?where=news&query=${encodeURIComponent(name)}`,
  }))
}

function makeDisclosures(name: string, limit: number): { title: string; url: string }[] {
  return Array.from({ length: limit }, (_, i) => ({
    title: `[샘플] ${name} ${DISCLOSURE_TYPES[i % DISCLOSURE_TYPES.length]}`,
    url: 'https://dart.fss.or.kr',
  }))
}

// ---------- 인증 (로그인 전이면 모든 보호된 API가 여기서 막힌다) ----------

interface MockUser {
  id: number
  username: string
  password: string
}

// 계정만 localStorage에 같이 저장해서 새로고침해도 로그인이 유지되게 한다(실제 요구사항이자
// 테스트 포인트). 리포트/관심종목/모의투자 데이터까지 영속화하지는 않는다 — 새로고침하면
// 그 세션에서 만든 데이터는 비워진다(메모리만 씀). 이 mock만의 한계이고 실제 백엔드는 DB에 다 남는다.
const MOCK_USERS_KEY = 'mock_auth_users'

function loadUsers(): { users: MockUser[]; nextUserId: number } {
  try {
    const raw = localStorage.getItem(MOCK_USERS_KEY)
    if (raw) {
      const parsed = JSON.parse(raw) as { users: MockUser[]; nextUserId: number }
      if (Array.isArray(parsed.users)) return parsed
    }
  } catch {
    // localStorage를 못 쓰면(프라이빗 모드 등) 이번 세션 메모리만으로 동작한다.
  }
  return { users: [], nextUserId: 1 }
}

const loaded = loadUsers()
const users: MockUser[] = loaded.users
let nextUserId = loaded.nextUserId

function saveUsers(): void {
  try {
    localStorage.setItem(MOCK_USERS_KEY, JSON.stringify({ users, nextUserId }))
  } catch {
    // no-op
  }
}

function normalizeUsername(raw: string): string {
  return raw.trim().toLowerCase()
}

function validateUsername(raw: string): string {
  const normalized = normalizeUsername(raw)
  if (!/^[a-z0-9_]{3,20}$/.test(normalized)) {
    throw new Error('아이디는 3~20자의 영문 소문자, 숫자, 밑줄(_)만 쓸 수 있어요.')
  }
  return normalized
}

function validatePassword(password: string): void {
  if (password.length < 8 || password.length > 128) {
    throw new Error('비밀번호는 8~128자여야 해요.')
  }
}

// 토큰 자체에 사용자 id를 인코딩한다(서명 없는 가짜 JWT). 그러면 토큰↔사용자 매핑을 따로 된
// 메모리에 저장할 필요가 없어서, 새로고침으로 모듈이 다시 로드돼도(= 그 매핑이 사라져도) 깨지지 않는다.
function issueToken(userId: number): string {
  return `mock.${userId}.${Math.random().toString(36).slice(2)}`
}

function toUserOut(user: MockUser): User {
  return { id: user.id, username: user.username }
}

// 보호된 API 진입점마다 호출한다. 토큰이 없거나 모르는 형식이면 실제 401과 같은 흐름(토큰 삭제 +
// AUTH_EXPIRED_EVENT)을 그대로 흉내 낸다 — mock이어도 AuthContext/RequireAuth 동작을 그대로 검증할 수 있다.
// (실제 토큰처럼 24시간 뒤 만료되는 건 흉내 내지 않는다.)
function currentUserId(): number {
  const token = getToken()
  const match = token?.match(/^mock\.(\d+)\./)
  const userId = match ? Number(match[1]) : undefined
  if (userId == null) {
    clearToken()
    window.dispatchEvent(new Event(AUTH_EXPIRED_EVENT))
    throw unauthorized()
  }
  return userId
}

async function signup(username: string, password: string): Promise<AuthResponse> {
  await delay(300)
  const normalized = validateUsername(username)
  validatePassword(password)
  if (users.some((u) => u.username === normalized)) {
    throw new Error('이미 사용 중인 아이디입니다.')
  }
  const user: MockUser = { id: nextUserId++, username: normalized, password }
  users.push(user)
  saveUsers()
  return { user: toUserOut(user), access_token: issueToken(user.id), token_type: 'bearer' }
}

async function login(username: string, password: string): Promise<AuthResponse> {
  await delay(300)
  const normalized = normalizeUsername(username)
  const user = users.find((u) => u.username === normalized && u.password === password)
  if (!user) throw new Error('아이디 또는 비밀번호가 올바르지 않습니다')
  return { user: toUserOut(user), access_token: issueToken(user.id), token_type: 'bearer' }
}

async function me(): Promise<User> {
  await delay(150)
  const userId = currentUserId()
  const user = users.find((u) => u.id === userId)
  if (!user) throw unauthorized()
  return toUserOut(user)
}

// ---------- 사용자별 데이터 (2026-10-07 로그인 도입 — 사용자마다 따로 분리) ----------

interface UserStore {
  reportStore: ReportDetail[]
  nextReportId: number
  watchlist: { ticker: string; added_at: string }[]
  cashBalance: number
  holdings: Map<string, { quantity: number; avg_price: number }>
  trades: Trade[]
  nextTradeId: number
}

const INITIAL_CASH = 10_000_000
const stores = new Map<number, UserStore>()

function storeFor(userId: number): UserStore {
  let s = stores.get(userId)
  if (!s) {
    s = {
      reportStore: [],
      nextReportId: 1,
      watchlist: [],
      cashBalance: INITIAL_CASH,
      holdings: new Map(),
      trades: [],
      nextTradeId: 1,
    }
    stores.set(userId, s)
  }
  return s
}

// ---------- 리서치 리포트 (실제 /api/research 계약을 흉내) ----------

function toListItem(report: ReportDetail): ReportListItem {
  const { report_id, previous_report_id, question, summary, company_name, created_at, used_tools } = report
  return { report_id, previous_report_id, question, summary, company_name, created_at, used_tools }
}

function summarize(answer: string): string {
  const flat = answer.replace(/\s+/g, ' ').trim()
  return flat.length > 200 ? `${flat.slice(0, 200)}...` : flat
}

// 백엔드 에이전트(backend/app/agent.py)의 도구 선택 규칙을 흉내 낸다.
async function askResearch(question: string, previousReportId?: number | null): Promise<ResearchResponse> {
  await delay(1200)
  const store = storeFor(currentUserId())

  let previous: ReportDetail | undefined
  if (previousReportId != null) {
    previous = store.reportStore.find((r) => r.report_id === previousReportId)
    if (!previous) throw notFoundReport(previousReportId)
  }

  const meta =
    COMPANY_META.find((c) => question.includes(c.name) || question.includes(c.ticker)) ??
    (previous ? findCompanyMeta(previous.company_name ?? '') : undefined)

  const wantsDisclosure = /공시|사업보고서|자사주|공식/.test(question)
  const wantsNews = /뉴스|이슈|왜|소식/.test(question)
  const wantsStock = /주가|등락|거래량|오른|올랐|내렸|내린|하락|상승|시세/.test(question)
  const useStock = meta != null && (wantsStock || (!wantsNews && !wantsDisclosure))

  const used_tools: string[] = []
  const sources: Source[] = []
  const summaryParts: string[] = []

  if (useStock && meta) {
    const latest = getPriceSeries(meta.ticker).at(-1)!
    used_tools.push('stock_tool')
    summaryParts.push(
      `최근 등락률은 ${latest.change_pct}%, 거래량은 ${latest.volume?.toLocaleString('ko-KR')}주예요.`,
    )
  }
  if (wantsNews && meta) {
    used_tools.push('news_tool')
    for (const item of makeNews(meta.name, 3)) {
      sources.push({
        tool: 'news_tool',
        type: 'news',
        title: item.title,
        company_names: [meta.name],
        company_filter: null,
        url: item.url,
      })
    }
    summaryParts.push('관련 뉴스를 확인했어요.')
  }
  if (wantsDisclosure && meta) {
    used_tools.push('disclosure_tool')
    for (const item of makeDisclosures(meta.name, 3)) {
      sources.push({
        tool: 'disclosure_tool',
        type: 'disclosure',
        title: item.title,
        company_names: [meta.name],
        company_filter: null,
        url: item.url,
      })
    }
    summaryParts.push('최근 공시를 확인했어요.')
  }

  const intro = previous ? `[샘플 응답 · "${previous.question}"에 이어서] ` : '[샘플 응답] '
  const answer = meta
    ? `${intro}${meta.name}에 대해 조회했어요. ${summaryParts.join(' ')}\n실제 백엔드가 연결되면 llama3.1:8b가 도구 결과를 바탕으로 만든 답변이 여기에 표시돼요.`
    : `${intro}도구를 호출하지 않고 바로 답하는 경우예요. 종목명(삼성전자, SK하이닉스, NAVER 등)을 넣어서 질문하면 근거(sources)도 함께 볼 수 있어요.`

  const report: ReportDetail = {
    report_id: store.nextReportId++,
    previous_report_id: previousReportId ?? null,
    question,
    answer,
    summary: summarize(answer),
    company_name: meta?.name ?? null,
    created_at: new Date().toISOString(),
    used_tools,
    sources,
    disclaimer: DISCLAIMER,
  }
  store.reportStore.push(report)

  return {
    answer,
    used_tools,
    sources,
    report_id: report.report_id,
    previous_report_id: report.previous_report_id,
    disclaimer: DISCLAIMER,
  }
}

async function listReports(limit = 20, offset = 0) {
  await delay(200)
  const store = storeFor(currentUserId())
  const items = store.reportStore.slice().reverse().slice(offset, offset + limit).map(toListItem)
  return { total: store.reportStore.length, items }
}

async function getReport(reportId: number) {
  await delay(150)
  const store = storeFor(currentUserId())
  const report = store.reportStore.find((r) => r.report_id === reportId)
  if (!report) throw notFoundReport(reportId)
  return report
}

async function deleteReport(reportId: number) {
  await delay(150)
  const store = storeFor(currentUserId())
  const index = store.reportStore.findIndex((r) => r.report_id === reportId)
  if (index === -1) throw notFoundReport(reportId)
  store.reportStore.splice(index, 1)
  // DB의 ON DELETE SET NULL 흉내: 이 리포트를 이어받던 후속 리포트의 링크를 끊는다.
  for (const r of store.reportStore) {
    if (r.previous_report_id === reportId) r.previous_report_id = null
  }
}

// ---------- 실시간 시세 (실제로는 네이버 비공식 폴링 API를 흉내) ----------

// 장중처럼 최근 종가 근처에서 살짝 흔들리는 "현재가"를 만든다. 호출마다 조금씩 바뀌어서
// 폴링(2~3초 간격)해도 값이 움직이는 걸 볼 수 있다.
function jitteredPrice(prevClose: number): number {
  const move = (Math.random() - 0.5) * 0.01
  return Math.max(100, Math.round((prevClose * (1 + move)) / 10) * 10)
}

async function getRealtimePrice(ticker: string): Promise<RealtimePrice> {
  await delay(150)
  const meta = findCompanyMeta(ticker)
  if (!meta) throw companyNotFound(ticker)

  const series = getPriceSeries(meta.ticker)
  const prevClose = series.at(-2)?.close_price ?? series.at(-1)!.close_price
  const current = jitteredPrice(series.at(-1)!.close_price)

  return {
    ticker: meta.ticker,
    company_name: meta.name,
    current_price: current,
    change_amount: Math.round((current - prevClose) * 100) / 100,
    change_pct: Math.round(((current - prevClose) / prevClose) * 10000) / 100,
    as_of: new Date().toISOString(),
    queried_at: new Date().toISOString(),
    is_realtime: true,
    source: 'naver',
    corrected_from: null,
  }
}

// ---------- 오늘의 관심 종목 ----------

const WINDOW_DAYS = 20

async function getTrending(category: TrendingCategory, limit = 10): Promise<TrendingResponse> {
  await delay(200)

  const items: TrendingItem[] = COMPANY_META.map((meta) => {
    const series = getPriceSeries(meta.ticker)
    const latest = series.at(-1)!
    const window = series.slice(-1 - WINDOW_DAYS, -1)
    const avgVolume = window.reduce((sum, p) => sum + (p.volume ?? 0), 0) / (window.length || 1)
    return {
      ticker: meta.ticker,
      company_name: meta.name,
      close_price: latest.close_price,
      change_pct: latest.change_pct,
      volume: latest.volume,
      avg_volume: Math.round(avgVolume),
      volume_ratio: avgVolume > 0 ? Math.round(((latest.volume ?? 0) / avgVolume) * 100) / 100 : null,
    }
  })

  const sorted =
    category === 'gainers'
      ? items.sort((a, b) => (b.change_pct ?? 0) - (a.change_pct ?? 0))
      : category === 'losers'
        ? items.sort((a, b) => (a.change_pct ?? 0) - (b.change_pct ?? 0))
        : items.sort((a, b) => (b.volume_ratio ?? 0) - (a.volume_ratio ?? 0))

  return {
    category,
    price_date: getPriceSeries(COMPANY_META[0].ticker).at(-1)!.price_date,
    window_days: WINDOW_DAYS,
    items: sorted.slice(0, limit),
  }
}

// ---------- 종목 검색 ----------

async function searchCompanies(q?: string, limit = 50): Promise<Company[]> {
  await delay(200)
  const needle = q?.trim().toLowerCase()
  const matched = needle
    ? COMPANY_META.filter((c) => c.name.toLowerCase().includes(needle) || c.ticker.includes(needle))
    : COMPANY_META
  return matched.slice(0, limit).map(companyView)
}

// ---------- 면책 문구 ----------

async function getDisclaimer(): Promise<string> {
  await delay(100)
  return DISCLAIMER
}

// ---------- 관심종목 ----------

async function listWatchlist(): Promise<WatchlistItem[]> {
  await delay(200)
  const store = storeFor(currentUserId())
  return store.watchlist
    .slice()
    .reverse()
    .map(({ ticker, added_at }) => {
      const meta = findCompanyMeta(ticker)!
      const latest = getPriceSeries(ticker).at(-1)
      return {
        company_id: COMPANY_META.indexOf(meta) + 1,
        ticker: meta.ticker,
        company_name: meta.name,
        added_at,
        latest_close: latest?.close_price ?? null,
        latest_close_date: latest?.price_date ?? null,
        change_pct: latest?.change_pct ?? null,
      }
    })
}

async function addWatchlistItem(ticker: string): Promise<WatchlistItem> {
  await delay(200)
  const store = storeFor(currentUserId())
  const meta = findCompanyMeta(ticker)
  if (!meta) throw companyNotFound(ticker)
  if (store.watchlist.some((w) => w.ticker === meta.ticker)) {
    throw new Error(`'${meta.name}'는 이미 관심종목에 등록되어 있습니다.`)
  }
  const added_at = new Date().toISOString()
  store.watchlist.push({ ticker: meta.ticker, added_at })
  // 실제 API와 동일하게, 추가 직후 응답에는 최근 종가를 다시 채워 넣지 않는다.
  return {
    company_id: COMPANY_META.indexOf(meta) + 1,
    ticker: meta.ticker,
    company_name: meta.name,
    added_at,
    latest_close: null,
    latest_close_date: null,
    change_pct: null,
  }
}

async function removeWatchlistItem(ticker: string): Promise<void> {
  await delay(200)
  const store = storeFor(currentUserId())
  const meta = findCompanyMeta(ticker)
  if (!meta) throw companyNotFound(ticker)
  const index = store.watchlist.findIndex((w) => w.ticker === meta.ticker)
  if (index === -1) throw new Error(`'${meta.name}'는 관심종목에 등록되어 있지 않습니다.`)
  store.watchlist.splice(index, 1)
}

// ---------- 모의투자 ----------

function buildPortfolio(store: UserStore): Portfolio {
  const holdingItems: Holding[] = []
  for (const [ticker, { quantity, avg_price }] of store.holdings) {
    const meta = findCompanyMeta(ticker)!
    const current = getPriceSeries(ticker).at(-1)!.close_price
    const eval_amount = Math.round(current * quantity)
    holdingItems.push({
      ticker: meta.ticker,
      company_name: meta.name,
      quantity,
      avg_price,
      current_price: current,
      is_realtime: true,
      eval_amount,
      profit_loss: Math.round(eval_amount - avg_price * quantity),
      profit_loss_pct: Math.round(((current - avg_price) / avg_price) * 10000) / 100,
    })
  }
  const total_eval_amount = holdingItems.reduce((sum, h) => sum + (h.eval_amount ?? 0), 0)
  return {
    cash_balance: store.cashBalance,
    holdings: holdingItems,
    total_eval_amount,
    total_asset: store.cashBalance + total_eval_amount,
    note: null,
  }
}

async function getPortfolio(): Promise<Portfolio> {
  await delay(200)
  return buildPortfolio(storeFor(currentUserId()))
}

async function placeOrder(ticker: string, side: 'buy' | 'sell', quantity: number): Promise<OrderResult> {
  await delay(300)
  const store = storeFor(currentUserId())
  const meta = findCompanyMeta(ticker)
  if (!meta) throw companyNotFound(ticker)

  const price = getPriceSeries(meta.ticker).at(-1)!.close_price
  const existing = store.holdings.get(meta.ticker)

  if (side === 'buy') {
    const cost = price * quantity
    if (cost > store.cashBalance) {
      throw new Error(
        `잔고가 부족합니다(필요 ${Math.round(cost).toLocaleString('ko-KR')}원, 보유 ${store.cashBalance.toLocaleString('ko-KR')}원).`,
      )
    }
    store.cashBalance -= cost
    const newQuantity = (existing?.quantity ?? 0) + quantity
    const avg_price = existing ? (existing.quantity * existing.avg_price + quantity * price) / newQuantity : price
    store.holdings.set(meta.ticker, { quantity: newQuantity, avg_price })
  } else {
    if (!existing || existing.quantity < quantity) {
      throw new Error(`보유 수량이 부족합니다(매도 요청 ${quantity}주, 보유 ${existing?.quantity ?? 0}주).`)
    }
    store.cashBalance += price * quantity
    const remaining = existing.quantity - quantity
    if (remaining === 0) store.holdings.delete(meta.ticker)
    else store.holdings.set(meta.ticker, { ...existing, quantity: remaining })
  }

  store.trades.push({
    id: store.nextTradeId++,
    ticker: meta.ticker,
    company_name: meta.name,
    side,
    quantity,
    price,
    amount: Math.round(price * quantity),
    executed_at: new Date().toISOString(),
  })

  const after = store.holdings.get(meta.ticker)
  return {
    ticker: meta.ticker,
    company_name: meta.name,
    side,
    quantity,
    price,
    executed_at: new Date().toISOString(),
    cash_balance: store.cashBalance,
    holding: after ? { quantity: after.quantity, avg_price: after.avg_price } : null,
  }
}

async function getTrades(limit = 50, offset = 0, ticker?: string): Promise<TradeList> {
  await delay(200)
  const store = storeFor(currentUserId())
  let filtered = store.trades
  if (ticker) {
    const meta = findCompanyMeta(ticker)
    if (!meta) throw companyNotFound(ticker)
    filtered = filtered.filter((t) => t.ticker === meta.ticker)
  }
  const sorted = filtered.slice().reverse() // 최신순(트레이드는 체결 순서대로 쌓인다)
  return { total: sorted.length, limit, offset, items: sorted.slice(offset, offset + limit) }
}

async function resetPortfolio(): Promise<Portfolio> {
  await delay(300)
  const store = storeFor(currentUserId())
  store.holdings.clear()
  store.trades = []
  store.cashBalance = INITIAL_CASH
  return buildPortfolio(store)
}

// ---------- 포트폴리오 AI 진단 ----------
// 실제 서버는 LLM(llama3.1:8b)이 설명을 쓰고 실패하면 규칙 기반으로 폴백하는데(source로 구분),
// mock은 항상 규칙 기반 문장만 만든다 — 가짜 LLM 말투를 흉내 내는 게 오히려 오해를 부른다.

const FLAG_RULES = {
  CONCENTRATION_HIGH: 40,
  CASH_HIGH: 70,
  CASH_LOW: 5,
  BIG_LOSS: -10,
  MARKET_SKEW: 80,
} as const

async function getDiagnosis(): Promise<DiagnosisResponse> {
  await delay(1500)
  const store = storeFor(currentUserId())
  if (store.holdings.size === 0) {
    throw new Error('보유 종목이 없습니다. 모의투자 주문 후 진단할 수 있어요.')
  }

  const holdings: DiagnosisHolding[] = []
  const marketEval: Record<string, number> = {}
  let stockEvalAmount = 0
  let totalCost = 0
  let totalProfitLoss = 0

  for (const [ticker, { quantity, avg_price }] of store.holdings) {
    const meta = findCompanyMeta(ticker)!
    const series = getPriceSeries(ticker)
    const current = series.at(-1)!.close_price
    const eval_amount = Math.round(current * quantity)
    const profit_loss = Math.round(eval_amount - avg_price * quantity)
    const profit_loss_pct = Math.round(((current - avg_price) / avg_price) * 10000) / 100
    const past = series.length >= 21 ? series.at(-21)! : null
    const return_20d_pct = past ? Math.round(((current - past.close_price) / past.close_price) * 10000) / 100 : null

    stockEvalAmount += eval_amount
    totalCost += avg_price * quantity
    totalProfitLoss += profit_loss
    marketEval[meta.market] = (marketEval[meta.market] ?? 0) + eval_amount

    holdings.push({
      ticker: meta.ticker,
      company_name: meta.name,
      market: meta.market,
      quantity,
      avg_price,
      current_price: current,
      is_realtime: true,
      price_unavailable: false,
      eval_amount,
      weight_pct: 0, // 아래에서 총자산 계산 후 채운다
      profit_loss,
      profit_loss_pct,
      return_20d_pct,
    })
  }

  const totalAsset = store.cashBalance + stockEvalAmount
  for (const h of holdings) {
    h.weight_pct = totalAsset > 0 ? Math.round(((h.eval_amount ?? 0) / totalAsset) * 10000) / 100 : null
  }

  const byWeightDesc = holdings.slice().sort((a, b) => (b.eval_amount ?? 0) - (a.eval_amount ?? 0))
  const top1WeightPct = byWeightDesc[0]?.weight_pct ?? null
  const top3WeightPct =
    totalAsset > 0
      ? Math.round(
          (byWeightDesc.slice(0, 3).reduce((sum, h) => sum + (h.eval_amount ?? 0), 0) / totalAsset) * 10000,
        ) / 100
      : null

  const herfindahlIndex =
    stockEvalAmount > 0
      ? Math.round(
          holdings.reduce((sum, h) => sum + ((h.eval_amount ?? 0) / stockEvalAmount) ** 2, 0) * 1000,
        ) / 1000
      : null

  const marketWeightsPct: Record<string, number | null> = {}
  for (const [market, amount] of Object.entries(marketEval)) {
    marketWeightsPct[market] = stockEvalAmount > 0 ? Math.round((amount / stockEvalAmount) * 10000) / 100 : null
  }

  const byProfitDesc = holdings.slice().sort((a, b) => (b.profit_loss_pct ?? 0) - (a.profit_loss_pct ?? 0))
  const bestHolding = byProfitDesc[0]
    ? { ticker: byProfitDesc[0].ticker, company_name: byProfitDesc[0].company_name, profit_loss_pct: byProfitDesc[0].profit_loss_pct }
    : null
  const worstHolding = byProfitDesc.at(-1)
    ? {
        ticker: byProfitDesc.at(-1)!.ticker,
        company_name: byProfitDesc.at(-1)!.company_name,
        profit_loss_pct: byProfitDesc.at(-1)!.profit_loss_pct,
      }
    : null

  const cashWeightPct = totalAsset > 0 ? Math.round((store.cashBalance / totalAsset) * 10000) / 100 : null

  const flags: DiagnosisFlag[] = []
  if (top1WeightPct != null && top1WeightPct >= FLAG_RULES.CONCENTRATION_HIGH) {
    flags.push({
      code: 'CONCENTRATION_HIGH',
      message: `상위 1종목(${byWeightDesc[0].company_name}) 비중이 ${top1WeightPct}%로 높아요.`,
      value: top1WeightPct,
      threshold: FLAG_RULES.CONCENTRATION_HIGH,
      ticker: byWeightDesc[0].ticker,
      company_name: byWeightDesc[0].company_name,
      market: null,
    })
  }
  if (holdings.length < 3) {
    flags.push({
      code: 'FEW_HOLDINGS',
      message: `보유 종목이 ${holdings.length}개로 적어요.`,
      value: holdings.length,
      threshold: 3,
      ticker: null,
      company_name: null,
      market: null,
    })
  }
  if (cashWeightPct != null && cashWeightPct >= FLAG_RULES.CASH_HIGH) {
    flags.push({
      code: 'CASH_HIGH',
      message: `현금 비중이 ${cashWeightPct}%로 높아요.`,
      value: cashWeightPct,
      threshold: FLAG_RULES.CASH_HIGH,
      ticker: null,
      company_name: null,
      market: null,
    })
  }
  if (cashWeightPct != null && cashWeightPct < FLAG_RULES.CASH_LOW) {
    flags.push({
      code: 'CASH_LOW',
      message: `현금 비중이 ${cashWeightPct}%로 낮아요.`,
      value: cashWeightPct,
      threshold: FLAG_RULES.CASH_LOW,
      ticker: null,
      company_name: null,
      market: null,
    })
  }
  for (const h of holdings) {
    if (h.profit_loss_pct != null && h.profit_loss_pct <= FLAG_RULES.BIG_LOSS) {
      flags.push({
        code: 'BIG_LOSS',
        message: `${h.company_name} 손익률이 ${h.profit_loss_pct}%예요.`,
        value: h.profit_loss_pct,
        threshold: FLAG_RULES.BIG_LOSS,
        ticker: h.ticker,
        company_name: h.company_name,
        market: null,
      })
    }
  }
  for (const [market, pct] of Object.entries(marketWeightsPct)) {
    if (pct != null && pct >= FLAG_RULES.MARKET_SKEW) {
      flags.push({
        code: 'MARKET_SKEW',
        message: `${market} 비중이 ${pct}%로 한쪽에 쏠려 있어요.`,
        value: pct,
        threshold: FLAG_RULES.MARKET_SKEW,
        ticker: null,
        company_name: null,
        market,
      })
    }
  }

  const totalProfitLossPct = totalCost > 0 ? Math.round((totalProfitLoss / totalCost) * 10000) / 100 : null

  const summary =
    `현재 포트폴리오의 총자산은 ${Math.round(totalAsset).toLocaleString('ko-KR')}원이며, ` +
    `현금 비중은 ${cashWeightPct ?? '-'}%입니다. 보유 종목은 ${holdings.length}개이고 ` +
    `총 평가손익은 ${Math.round(totalProfitLoss).toLocaleString('ko-KR')}원(${totalProfitLossPct ?? '-'}%)입니다.`

  const strengths =
    flags.length === 0
      ? [`상위 1종목 비중이 ${top1WeightPct ?? '-'}%로 한 종목에 크게 쏠려 있지 않습니다.`, `${holdings.length}개 종목에 나눠 보유하고 있습니다.`]
      : [`${holdings.length}개 종목에 나눠 보유하고 있습니다.`]

  const risks =
    flags.length > 0 ? flags.map((f) => f.message) : ['규칙 기준으로 확인된 위험 항목은 없습니다.']

  const suggestions = [
    '종목별 뉴스와 공시를 리서치 탭에서 주기적으로 확인해볼 수 있습니다.',
    ...(flags.some((f) => f.code === 'CONCENTRATION_HIGH' || f.code === 'MARKET_SKEW')
      ? ['비중이 쏠린 종목·시장을 분산하는 것을 고려해볼 수 있습니다.']
      : []),
  ]

  return {
    generated_at: new Date().toISOString(),
    source: 'rule_based',
    model: null,
    metrics: {
      total_asset: Math.round(totalAsset),
      cash_balance: store.cashBalance,
      cash_weight_pct: cashWeightPct,
      stock_eval_amount: stockEvalAmount,
      holding_count: holdings.length,
      priced_holding_count: holdings.length,
      top1_weight_pct: top1WeightPct,
      top3_weight_pct: top3WeightPct,
      herfindahl_index: herfindahlIndex,
      market_weights_pct: marketWeightsPct,
      total_profit_loss: Math.round(totalProfitLoss),
      total_profit_loss_pct: totalProfitLossPct,
      best_holding: bestHolding,
      worst_holding: worstHolding,
    },
    holdings,
    flags,
    summary,
    strengths,
    risks,
    suggestions,
    notes: ['샘플 데이터 기반 규칙 진단이에요(실제 백엔드의 LLM 설명과는 다릅니다).'],
    disclaimer: DISCLAIMER,
  }
}

export const mockApi: Api = {
  signup,
  login,
  me,

  askResearch,
  listReports,
  getReport,
  deleteReport,

  getRealtimePrice,
  getTrending,
  searchCompanies,
  getDisclaimer,

  listWatchlist,
  addWatchlistItem,
  removeWatchlistItem,
  getPortfolio,
  placeOrder,
  getTrades,
  resetPortfolio,
  getDiagnosis,
}
