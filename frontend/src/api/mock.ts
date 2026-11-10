import { ApiError } from './errors'
import { AUTH_EXPIRED_EVENT, clearToken, getToken } from '../utils/token'
import type {
  AssetType,
  AuthResponse,
  Company,
  DiagnosisChanges,
  DiagnosisCompareResponse,
  DiagnosisDetail,
  DiagnosisFlag,
  DiagnosisFlagSummary,
  DiagnosisHistoryItem,
  DiagnosisHistoryList,
  DiagnosisHolding,
  DiagnosisHoldingChange,
  DiagnosisResponse,
  DiagnosisReturnChange,
  DiagnosisTotalAssetChange,
  EventReaction,
  EventReactionsResponse,
  EventReturn,
  EventType,
  EventTypeFilter,
  Holding,
  OrderResult,
  Portfolio,
  PriceHistoryResponse,
  RealtimePrice,
  ReportDetail,
  ReportListItem,
  ResearchResponse,
  Source,
  StatusResponse,
  Trade,
  TradeList,
  TrendingCategory,
  TrendingItem,
  TrendingResponse,
  User,
  WatchlistItem,
} from '../types'
import type { BaseApi } from './index'
import { DEFAULT_LOCALE, LOCALES, isLocale } from './contracts/common'
import type { AnalysisType, Locale, TranslationStatus } from './contracts/common'
import type { Preferences, PreferencesInput } from './contracts/preferences'
import type { DataBasisItem, ReportRecordDetail } from './contracts/research'
import { translate } from '../i18n'
import type { ExposureChanges } from './contracts/diagnosis'
import type { LookThroughSnapshot } from './contracts/exposure'
import { computeLookThrough, toSnapshot } from './mock-exposure-core'
import type { CompanyMeta } from './mock-core'
import { researchText } from './mock-research-text'

import {
  COMPANY_META,
  DEFAULT_TIMEZONE,
  DISCLAIMER,
  companyNotFound,
  companyView,
  delay,
  findCompanyMeta,
  getPriceSeries,
  dateGroupOf,
  isLeveragedOrInverseEtf,
  isValidTimezone,
  kstDate,
  josaEunNeun,
  localParts,
  makeDisclosures,
  makeNews,
  mockNow,
  mulberry32,
  notFoundReport,
  unauthorized,
} from './mock-core'

// ---------- 인증 (로그인 전이면 모든 보호된 API가 여기서 막힌다) ----------

export interface MockUser {
  id: number
  username: string
  password: string
  // 이메일 복구 mock(mock-account.ts). 없으면 이메일을 등록하지 않은 계정이다
  email?: string | null
  emailVerified?: boolean
  // 이 시각 전에 발급한 토큰은 무효다(비밀번호 변경·재설정 = 실제 서버의 token_version 증가)
  tokensValidFrom?: number
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
export const mockUsers = users
let nextUserId = loaded.nextUserId

export function saveUsers(): void {
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
    throw new ApiError('아이디는 3~20자의 영문 소문자, 숫자, 밑줄(_)만 쓸 수 있어요.', 422)
  }
  return normalized
}

// 실제 백엔드(2026-10-30)의 가입·변경·재설정 공통 규칙: 8~128자, 내장 목록의 흔한 비밀번호·한 문자 반복·아이디 일부(3자 이상) 거부.
// 흔한 비밀번호 목록 전체는 서버에 있고, mock은 대표적인 몇 개만 막는다(실제 서버에서 "password123"이 거부되는 걸 확인했다).
const COMMON_PASSWORDS = ['password', 'password1', 'password123', '12345678', '123456789', 'qwerty123', 'iloveyou', 'abc12345']

export function validatePassword(password: string, username?: string): void {
  if (password.length < 8 || password.length > 128) {
    throw new ApiError('비밀번호는 8~128자여야 해요.', 422)
  }
  const lower = password.toLowerCase()
  if (COMMON_PASSWORDS.includes(lower)) {
    throw new ApiError('너무 흔한 비밀번호입니다. 다른 비밀번호를 정해주세요.', 422)
  }
  if (new Set(password).size === 1) {
    throw new ApiError('같은 문자만 반복한 비밀번호는 사용할 수 없습니다.', 422)
  }
  if (username && username.length >= 3 && lower.includes(username.toLowerCase())) {
    throw new ApiError('아이디가 들어간 비밀번호는 사용할 수 없습니다.', 422)
  }
}

// 토큰 자체에 사용자 id와 발급 시각을 인코딩한다(서명 없는 가짜 JWT). 그러면 토큰↔사용자 매핑을
// 따로 된 메모리에 저장할 필요가 없어서, 새로고침으로 모듈이 다시 로드돼도(= 그 매핑이 사라져도)
// 깨지지 않는다. 발급 시각은 실제 JWT처럼 24시간 만료를 흉내 내는 데 쓴다.
const TOKEN_TTL_MS = 24 * 60 * 60 * 1000 // backend/API.md: access token은 24시간 만료

export function issueToken(userId: number): string {
  return `mock.${userId}.${Date.now()}.${Math.random().toString(36).slice(2)}`
}

export function toUserOut(user: MockUser): User {
  return { id: user.id, username: user.username }
}

// GET /auth/me는 이메일 상태까지 준다(가입·로그인 응답의 user에는 없다)
function toMeOut(user: MockUser): User {
  return { ...toUserOut(user), email: user.email ?? null, email_verified: user.emailVerified ?? false }
}

// 보호된 API 진입점마다 호출한다. 토큰이 없거나 모르는 형식이거나 발급 24시간이 지났으면 실제
// 401과 같은 흐름(토큰 삭제 + AUTH_EXPIRED_EVENT)을 그대로 흉내 낸다 — mock이어도 AuthContext/
// RequireAuth 동작을 그대로 검증할 수 있다.
export function currentUserId(): number {
  const token = getToken()
  const match = token?.match(/^mock\.(\d+)\.(\d+)\./)
  const userId = match ? Number(match[1]) : undefined
  const issuedAt = match ? Number(match[2]) : undefined
  const expired = issuedAt != null && Date.now() - issuedAt > TOKEN_TTL_MS
  // 비밀번호를 바꾸거나 재설정하면 그 전에 발급한 토큰은 모두 무효다(실제 서버의 token_version)
  const owner = users.find((u) => u.id === userId)
  const revoked = issuedAt != null && issuedAt < (owner?.tokensValidFrom ?? 0)
  // 탈퇴로 지워진 계정의 토큰도 무효다(실제 서버: 지운 뒤 그 사용자의 모든 JWT는 401)
  if (userId == null || !owner || expired || revoked) {
    clearToken()
    window.dispatchEvent(new Event(AUTH_EXPIRED_EVENT))
    throw unauthorized()
  }
  return userId
}

async function signup(username: string, password: string): Promise<AuthResponse> {
  await delay(300)
  const normalized = validateUsername(username)
  validatePassword(password, normalized)
  if (users.some((u) => u.username === normalized)) {
    throw new ApiError('이미 사용 중인 아이디입니다.', 409)
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
  if (!user) throw new ApiError('아이디 또는 비밀번호가 올바르지 않습니다', 401)
  return { user: toUserOut(user), access_token: issueToken(user.id), token_type: 'bearer' }
}

async function me(): Promise<User> {
  await delay(150)
  const userId = currentUserId()
  const user = users.find((u) => u.id === userId)
  if (!user) throw unauthorized()
  return toMeOut(user)
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
  // 진단 이력(2026-10-14 추가). 실제 백엔드처럼 최근 50건만 보관한다.
  diagnosisHistory: DiagnosisDetail[]
  nextDiagnosisId: number
  // 언어·시간대 설정(GET/PUT /api/auth/preferences). 기존 사용자는 ko-KR, 시간대 null(= Asia/Seoul).
  preferences: { locale: Locale; timezone: string | null }
  // 리포트별 기록 정보(view=record): 작성 언어·번역 상태·한국어 원문·분석 유형·기준일. 기본 응답(ReportDetail)에는 없는 값이다.
  reportRecords: Map<number, ReportRecordExtra>
}

export interface ReportRecordExtra {
  locale: Locale
  translation_status: TranslationStatus | null
  original_answer: string | null
  analysis_types: AnalysisType[]
  data_basis: DataBasisItem[]
}

const INITIAL_CASH = 10_000_000
const MAX_DIAGNOSIS_HISTORY = 50
const stores = new Map<number, UserStore>()

export function storeFor(userId: number): UserStore {
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
      diagnosisHistory: [],
      nextDiagnosisId: 1,
      preferences: { locale: DEFAULT_LOCALE, timezone: null },
      reportRecords: new Map(),
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
interface AnswerContext {
  meta: CompanyMeta | undefined
  previousQuestion: string | undefined
  useStock: boolean
  wantsWhy: boolean
  evidenceTitles: string[]
  changePct: number | null
  volume: string
}

// 같은 구조("## 섹션")의 답을 언어별로 만든다. 한국어 답이 "원문"이고, 다른 언어 답은 번역본 흉내다.
function buildAnswer(locale: Locale, ctx: AnswerContext): string {
  const t = researchText(locale)
  const intro = ctx.previousQuestion ? t.introFollowUp(ctx.previousQuestion) : t.intro
  if (ctx.meta == null) return `${intro} ${t.noTool}`

  const sections: string[] = []
  if (ctx.useStock) {
    sections.push(`## ${t.price}\n${t.priceLine(ctx.meta.name, ctx.changePct, ctx.volume)}`)
  }
  if (ctx.wantsWhy) sections.push(`## ${t.cause}\n${t.causeLine}`)
  if (ctx.evidenceTitles.length > 0) {
    sections.push(`## ${t.evidence}\n${ctx.evidenceTitles.map((title) => `- ${title}`).join('\n')}`)
  }
  return sections.length > 0 ? `${intro}\n\n${sections.join('\n\n')}` : `${intro} ${t.genericLookup(ctx.meta.name)}`
}

function classifyAnalysis(question: string, meta: CompanyMeta | undefined, tools: string[]): AnalysisType[] {
  const types: AnalysisType[] = []
  if (/전망|예상|오를까|내릴까/.test(question)) types.push('outlook')
  if (meta?.assetType === 'etf') types.push('etf')
  if (tools.includes('disclosure_tool')) types.push('disclosure')
  if (tools.includes('news_tool')) types.push('news')
  if (tools.includes('stock_tool')) types.push('price')
  return types.length > 0 ? types : ['general']
}

async function askResearch(question: string, previousReportId?: number | null): Promise<ResearchResponse> {
  await delay(1200)
  const store = storeFor(currentUserId())
  const locale = store.preferences.locale

  let previous: ReportDetail | undefined
  if (previousReportId != null) {
    previous = store.reportStore.find((r) => r.report_id === previousReportId)
    if (!previous) throw notFoundReport(previousReportId)
  }

  const meta =
    COMPANY_META.find((c) => question.includes(c.name) || question.includes(c.ticker)) ??
    (previous ? findCompanyMeta(previous.company_name ?? '') : undefined)

  const wantsWhy = /왜|이유|원인/.test(question)
  const wantsDisclosure = /공시|사업보고서|자사주|공식/.test(question)
  const wantsNews = /뉴스|이슈|왜|소식/.test(question)
  const wantsStock = /주가|등락|거래량|오른|올랐|내렸|내린|하락|상승|시세/.test(question)
  const useStock = meta != null && (wantsStock || (!wantsNews && !wantsDisclosure))

  const used_tools: string[] = []
  const sources: Source[] = []
  const evidenceTitles: string[] = []
  const dataBasis: DataBasisItem[] = []

  const latest = meta ? getPriceSeries(meta.ticker).at(-1)! : undefined
  if (useStock && meta && latest) {
    used_tools.push('stock_tool')
    dataBasis.push({ tool: 'stock_tool', company_name: meta.name, ticker: meta.ticker, price_as_of: latest.price_date })
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
      evidenceTitles.push(item.title)
    }
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
      evidenceTitles.push(item.title)
    }
  }

  const ctx: AnswerContext = {
    meta,
    previousQuestion: previous?.question,
    useStock,
    wantsWhy: wantsWhy && meta != null,
    evidenceTitles,
    changePct: latest?.change_pct ?? null,
    volume: latest?.volume?.toLocaleString('ko-KR') ?? '-',
  }

  // 백엔드는 모든 답을 한국어로 먼저 만든다(숫자 검증 등 전부 적용). ko-KR이 아니면 그 답을 번역(translated)하거나,
  // 코드가 그 언어로 바로 쓰거나(localized: 도구 없이 답하는 안내 등), 번역 검증에 실패하면(fallback_summary)
  // 그 언어 안내 + 데이터 요약만 준다. mock은 질문에 "번역실패"가 들어 있으면 fallback_summary 상태를 보여준다.
  const koAnswer = buildAnswer('ko-KR', ctx)
  let answer = koAnswer
  let translationStatus: TranslationStatus | null = null
  let originalAnswer: string | null = null
  if (locale !== DEFAULT_LOCALE) {
    const t = researchText(locale)
    if (meta == null) {
      answer = buildAnswer(locale, ctx)
      translationStatus = 'localized'
    } else if (question.includes('번역실패')) {
      const summary = useStock ? t.priceLine(meta.name, ctx.changePct, ctx.volume) : t.genericLookup(meta.name)
      answer = `${t.fallbackNote}\n\n## ${t.fallbackHeading}\n${summary}`
      translationStatus = 'fallback_summary'
      originalAnswer = koAnswer
    } else {
      answer = `${buildAnswer(locale, ctx)}\n\n${t.translatedNote}`
      translationStatus = 'translated'
      originalAnswer = koAnswer
    }
  }

  const disclaimer = translate(locale, 'system.disclaimer', { fallback: DISCLAIMER })
  const report: ReportDetail = {
    report_id: store.nextReportId++,
    previous_report_id: previousReportId ?? null,
    question,
    answer,
    summary: summarize(answer),
    company_name: meta?.name ?? null,
    created_at: mockNow().toISOString(),
    used_tools,
    sources,
    disclaimer,
  }
  store.reportStore.push(report)
  store.reportRecords.set(report.report_id, {
    locale,
    translation_status: translationStatus,
    original_answer: originalAnswer,
    analysis_types: classifyAnalysis(question, meta, used_tools),
    data_basis: dataBasis,
  })

  return {
    answer,
    used_tools,
    sources,
    report_id: report.report_id,
    previous_report_id: report.previous_report_id,
    disclaimer,
    locale,
    translation_status: translationStatus,
  }
}

// 리포트 기록 정보(view=record) - 제목·종목·분석 유형·기준일·작성 언어·번역 상태·한국어 원문.
export function toRecordItem(report: ReportDetail, store: UserStore) {
  const extra = store.reportRecords.get(report.report_id)
  const timeZone = store.preferences.timezone ?? DEFAULT_TIMEZONE
  const local = localParts(report.created_at, timeZone)
  const meta = report.company_name ? findCompanyMeta(report.company_name) : undefined
  const types = extra?.analysis_types ?? ['general']
  return {
    report_id: report.report_id,
    previous_report_id: report.previous_report_id,
    question: report.question,
    summary: report.summary,
    company_name: report.company_name,
    created_at: report.created_at,
    used_tools: report.used_tools,
    title: report.question.length > 60 ? `${report.question.slice(0, 60)}…` : report.question,
    company_names: meta ? [meta.name] : [],
    tickers: meta ? [meta.ticker] : [],
    analysis_type: types[0],
    analysis_types: types,
    source_count: report.sources.length,
    local_date: local.date,
    local_time: local.time,
    date_group: dateGroupOf(local.date, timeZone, mockNow()),
    locale: extra?.locale ?? DEFAULT_LOCALE,
  }
}

async function getReportRecord(reportId: number): Promise<ReportRecordDetail> {
  await delay(150)
  const store = storeFor(currentUserId())
  const report = store.reportStore.find((r) => r.report_id === reportId)
  if (!report) throw notFoundReport(reportId)
  const extra = store.reportRecords.get(reportId)
  return {
    ...toRecordItem(report, store),
    answer: report.answer,
    sources: report.sources,
    disclaimer: report.disclaimer,
    data_basis: extra?.data_basis ?? [],
    timezone: store.preferences.timezone ?? DEFAULT_TIMEZONE,
    translation_status: extra?.translation_status ?? null,
    original_answer: extra?.original_answer ?? null,
  }
}

async function logout(): Promise<void> {
  await delay(100)
  // 실제 백엔드는 이 사용자의 모든 토큰을 폐기한다. mock의 토큰은 만료 시각만 보므로 호출 자체가 전부다.
}

async function getPreferences(): Promise<Preferences> {
  await delay(100)
  const { preferences } = storeFor(currentUserId())
  return {
    preferred_locale: preferences.locale,
    preferred_timezone: preferences.timezone,
    effective_timezone: preferences.timezone ?? DEFAULT_TIMEZONE,
    supported_locales: [...LOCALES],
    default_locale: DEFAULT_LOCALE,
  }
}

async function updatePreferences(input: PreferencesInput): Promise<Preferences> {
  await delay(150)
  const store = storeFor(currentUserId())
  // 실제 백엔드처럼 지원하지 않는 값은 422다(GET의 locale 파라미터는 조용히 ko-KR로 가지만, 저장만 422).
  if (input.preferred_locale !== undefined && !isLocale(input.preferred_locale)) {
    throw new ApiError(`지원하지 않는 언어입니다: ${LOCALES.join(', ')} 중 하나`, 422)
  }
  if (input.preferred_timezone != null && !isValidTimezone(input.preferred_timezone)) {
    throw new ApiError(`알 수 없는 시간대입니다: ${input.preferred_timezone}`, 422)
  }
  if (input.preferred_locale !== undefined) store.preferences.locale = input.preferred_locale as Locale
  // 필드를 안 보내면 그대로, null을 보내면 기본(Asia/Seoul)으로 돌아간다.
  if (input.preferred_timezone !== undefined) store.preferences.timezone = input.preferred_timezone
  return getPreferences()
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

// ---------- 데이터 기준일 / 사건 이후 주가 반응 ----------

async function getStatus(): Promise<StatusResponse> {
  await delay(100)
  const latest = getPriceSeries('005930').at(-1)!.price_date
  return {
    generated_at: new Date().toISOString(),
    sources: {
      stock_price: { latest_date: latest },
      etf_price: { latest_date: latest },
      market_index: { latest_date: latest, stale: false },
      exchange_rate: { latest_date: latest },
      news: { latest_published_date: latest },
      disclosure: { latest_date: latest },
    },
    last_refresh: { finished_at: new Date().toISOString(), ok: true, failed_steps: [] },
    is_realtime: false,
    note: '샘플 데이터예요. 주가는 종가 기준이며, 실시간 시세는 별도로 제공돼요.',
  }
}

const EVENT_REACTION_DISCLAIMER =
  '사건 이후의 주가 움직임은 시간적 선후관계일 뿐 인과관계가 아니에요. 같은 기간의 시장 전체 흐름이나 다른 사건이 영향을 줬을 수 있고 표본이 적을 수 있어요. (샘플 데이터)'

// 시리즈 끝에서 거꾸로 센 거래일 수로 사건 위치를 고정해 두면 같은 종목은 항상 같은 결과가 나온다.
const EVENT_OFFSETS: { type: EventType; back: number }[] = [
  { type: 'disclosure', back: 2 },
  { type: 'news', back: 4 },
  { type: 'disclosure', back: 9 },
  { type: 'news', back: 13 },
  { type: 'news', back: 22 },
  { type: 'disclosure', back: 31 },
  { type: 'news', back: 47 },
  { type: 'disclosure', back: 58 },
]

const round2 = (n: number) => Math.round(n * 100) / 100

async function getEventReactions(
  ticker: string,
  options: { eventType?: EventTypeFilter; limit?: number; sinceDays?: number } = {},
): Promise<EventReactionsResponse> {
  await delay(200)
  const meta = findCompanyMeta(ticker)
  if (!meta) throw companyNotFound(ticker)
  const { eventType = 'all', limit = 10, sinceDays = 60 } = options
  if (!Number.isInteger(limit) || limit < 1 || limit > 30 || !Number.isInteger(sinceDays) || sinceDays < 1 || sinceDays > 120) {
    throw new ApiError('limit은 1~30, since_days는 1~120 사이여야 해요.', 422)
  }

  const series = getPriceSeries(meta.ticker)
  const isEtf = meta.assetType === 'etf'
  const news = makeNews(meta.name, 8)
  const disclosures = makeDisclosures(meta.name, 8)
  // since_days는 달력 일수라 거래일로는 약 70%
  const windowStart = series[Math.max(0, series.length - Math.round(sinceDays * 0.7))].price_date

  const events: EventReaction[] = []
  let newsIdx = 0
  let disclosureIdx = 0
  for (const { type, back } of EVENT_OFFSETS) {
    if (isEtf && type === 'disclosure') continue // ETF는 뉴스만 대상
    const idx = series.length - back
    if (idx < 1 || series[idx].price_date < windowStart) continue
    const item = type === 'news' ? news[newsIdx++ % news.length] : disclosures[disclosureIdx++ % disclosures.length]
    const base = series[idx - 1] // 기준일 = 사건 직전 거래일(샘플은 시각 규칙을 단순화)
    const returnAt = (n: number): EventReturn => {
      const target = series[idx + n - 1]
      if (!target) return { pct: null, index_pct: null, excess_pct: null, available: false, reason: `${n}거래일 경과 전` }
      const pct = round2(((target.close_price - base.close_price) / base.close_price) * 100)
      if (isEtf) {
        return { pct, index_pct: null, excess_pct: null, available: true, reason: 'ETF는 지수 대비 초과수익률을 계산하지 않아요' }
      }
      const indexPct = round2(pct * 0.55 + (((idx + n) % 5) - 2) * 0.3)
      return { pct, index_pct: indexPct, excess_pct: round2(pct - indexPct), available: true, reason: null }
    }
    const sampleDays = Math.min(20, idx)
    const avgVolume = series.slice(idx - sampleDays, idx).reduce((a, p) => a + (p.volume ?? 0), 0) / sampleDays
    events.push({
      event_type: type,
      title: item.title,
      url: item.url,
      event_at: type === 'news' ? `${series[idx].price_date}T${String(9 + (back % 6)).padStart(2, '0')}:20:00+09:00` : null,
      event_date: type === 'disclosure' ? series[idx].price_date : null,
      time_known: type === 'news',
      base_date: base.price_date,
      base_close: base.close_price,
      reaction_start_date: series[idx].price_date,
      returns: { d1: returnAt(1), d5: returnAt(5), d20: returnAt(20) },
      volume_ratio: avgVolume > 0 ? round2((series[idx].volume ?? 0) / avgVolume) : null,
      volume_sample_days: sampleDays,
      other_events_in_window: (back * 7) % 5,
      note: null,
    })
  }

  // limit은 유형별 최대 건수(최신순). EVENT_OFFSETS가 최신 → 오래된 순이라 앞에서부터 센다.
  const perType = new Map<EventType, number>()
  const selected = events
    .filter((e) => eventType === 'all' || e.event_type === eventType)
    .filter((e) => {
      const count = (perType.get(e.event_type) ?? 0) + 1
      perType.set(e.event_type, count)
      return count <= limit
    })

  const average = (values: (number | null)[]) => {
    const present = values.filter((v): v is number => v != null)
    return present.length ? round2(present.reduce((a, b) => a + b, 0) / present.length) : null
  }
  const stat = (key: 'd1' | 'd5' | 'd20') => ({
    withReturn: selected.filter((e) => e.returns[key].available).length,
    avg: average(selected.map((e) => e.returns[key].pct)),
    positive: selected.filter((e) => (e.returns[key].pct ?? 0) > 0).length,
    withExcess: selected.filter((e) => e.returns[key].excess_pct != null).length,
    avgExcess: average(selected.map((e) => e.returns[key].excess_pct)),
  })
  const d1 = stat('d1')
  const d5 = stat('d5')
  const d20 = stat('d20')

  return {
    ticker: meta.ticker,
    company_name: meta.name,
    asset_type: meta.assetType,
    price_as_of: series.at(-1)!.price_date,
    market_index: isEtf || !meta.market ? null : meta.market === 'KOSPI' ? 'KS11' : 'KQ11',
    events: selected,
    summary: {
      event_count: selected.length,
      with_d1: d1.withReturn,
      avg_d1_pct: d1.avg,
      positive_d1_count: d1.positive,
      with_excess_d1: d1.withExcess,
      avg_excess_d1_pct: d1.avgExcess,
      with_d5: d5.withReturn,
      avg_d5_pct: d5.avg,
      positive_d5_count: d5.positive,
      with_excess_d5: d5.withExcess,
      avg_excess_d5_pct: d5.avgExcess,
      with_d20: d20.withReturn,
      avg_d20_pct: d20.avg,
      positive_d20_count: d20.positive,
      with_excess_d20: d20.withExcess,
      avg_excess_d20_pct: d20.avgExcess,
    },
    disclaimer: EVENT_REACTION_DISCLAIMER,
  }
}

// ---------- 주가 추이(차트) ----------

// 실제 백엔드는 데이터 시작일이 있어서(주식 2026-06-26, ETF 2026-07-16) days=90이어도 개수가 모자란다
// (2026-10-18 기준 삼성전자 73개, KODEX 200 59개, backend/API.md). mock도 같은 한계를 흉내 내서
// 차트가 "90개를 가정"하는 코드로 굳지 않게 한다.
const MOCK_HISTORY_AVAILABLE = { stock: 73, etf: 59 } as const

async function getPriceHistory(ticker: string, days = 90): Promise<PriceHistoryResponse> {
  await delay(250)
  const meta = findCompanyMeta(ticker)
  if (!meta) throw companyNotFound(ticker)
  if (!Number.isInteger(days) || days < 1 || days > 90) {
    throw new ApiError('days는 1~90 사이의 정수여야 해요.', 422)
  }

  const series = getPriceSeries(meta.ticker)
  const available = series.slice(-MOCK_HISTORY_AVAILABLE[meta.assetType])
  const rows = available.slice(-days)
  const rand = mulberry32((Number(meta.ticker) || 1) + 7)
  const items = rows.map((row, i) => {
    const prevClose = i > 0 ? rows[i - 1].close_price : row.close_price
    const open = Math.round((prevClose * (1 + (rand() - 0.5) * 0.01)) / 10) * 10
    const high = Math.max(open, row.close_price) + Math.round(rand() * row.close_price * 0.008)
    const low = Math.min(open, row.close_price) - Math.round(rand() * row.close_price * 0.008)
    return {
      date: row.price_date,
      close: row.close_price,
      volume: row.volume,
      change_pct: row.change_pct,
      open,
      high,
      low,
    }
  })

  return {
    ticker: meta.ticker,
    company_name: meta.name,
    asset_type: meta.assetType,
    days,
    count: items.length,
    price_as_of: items.at(-1)?.date ?? null,
    items,
  }
}

// ---------- 오늘의 관심 종목 ----------

const WINDOW_DAYS = 20

async function getTrending(category: TrendingCategory, limit = 10, assetType: AssetType = 'stock'): Promise<TrendingResponse> {
  await delay(200)
  const candidates = COMPANY_META.filter((meta) => meta.assetType === assetType)

  const items: TrendingItem[] = candidates.map((meta) => {
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
    asset_type: assetType,
    price_date: getPriceSeries(candidates[0].ticker).at(-1)!.price_date,
    window_days: WINDOW_DAYS,
    items: sorted.slice(0, limit),
  }
}

// ---------- 종목 검색 ----------

async function searchCompanies(q?: string, limit = 50, assetType?: AssetType): Promise<Company[]> {
  await delay(200)
  const needle = q?.trim().toLowerCase()
  const byType = assetType ? COMPANY_META.filter((c) => c.assetType === assetType) : COMPANY_META
  const matched = needle
    ? byType.filter((c) => c.name.toLowerCase().includes(needle) || c.ticker.includes(needle))
    : byType
  return matched.slice(0, limit).map(companyView)
}

// ---------- 면책 문구 ----------

async function getDisclaimer(): Promise<string> {
  await delay(100)
  return DISCLAIMER
}

// ---------- 관심종목 ----------

// 목록(GET)과 추가(POST) 응답이 같은 값이 되도록 한곳에서 만든다(2026-10-11 백엔드와 동일하게).
function watchlistItemView(ticker: string, added_at: string): WatchlistItem {
  const meta = findCompanyMeta(ticker)!
  const latest = getPriceSeries(ticker).at(-1)
  return {
    company_id: COMPANY_META.indexOf(meta) + 1,
    ticker: meta.ticker,
    company_name: meta.name,
    asset_type: meta.assetType,
    added_at,
    latest_close: latest?.close_price ?? null,
    latest_close_date: latest?.price_date ?? null,
    change_pct: latest?.change_pct ?? null,
  }
}

async function listWatchlist(): Promise<WatchlistItem[]> {
  await delay(200)
  const store = storeFor(currentUserId())
  return store.watchlist
    .slice()
    .reverse()
    .map(({ ticker, added_at }) => watchlistItemView(ticker, added_at))
}

async function addWatchlistItem(ticker: string): Promise<WatchlistItem> {
  await delay(200)
  const store = storeFor(currentUserId())
  const meta = findCompanyMeta(ticker)
  if (!meta) throw companyNotFound(ticker)
  if (store.watchlist.some((w) => w.ticker === meta.ticker)) {
    throw new ApiError(`'${meta.name}'는 이미 관심종목에 등록되어 있습니다.`, 409)
  }
  const added_at = new Date().toISOString()
  store.watchlist.push({ ticker: meta.ticker, added_at })
  return watchlistItemView(meta.ticker, added_at)
}

async function removeWatchlistItem(ticker: string): Promise<void> {
  await delay(200)
  const store = storeFor(currentUserId())
  const meta = findCompanyMeta(ticker)
  if (!meta) throw companyNotFound(ticker)
  const index = store.watchlist.findIndex((w) => w.ticker === meta.ticker)
  if (index === -1) throw new ApiError(`'${meta.name}'는 관심종목에 등록되어 있지 않습니다.`, 404)
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
      asset_type: meta.assetType,
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
  // backend/API.md: quantity는 1 이상, 최대 100,000주인 정수여야 한다(422).
  if (!Number.isInteger(quantity) || quantity < 1 || quantity > 100_000) {
    throw new ApiError('수량은 1~100,000 사이의 정수여야 해요.', 422)
  }
  const meta = findCompanyMeta(ticker)
  if (!meta) throw companyNotFound(ticker)

  // 체결가 = 그 시점(mockNow)의 종가. 개발용 시계 훅(mock-core)으로 시계를 과거로 옮긴 상태에서 넣은 주문은 그날 종가로
  // 체결된 것으로 기록돼서, 성과 곡선 mock이 체결 내역을 다시 재생할 때 가격이 맞는다. 훅이 없으면 마지막 종가(전과 같음).
  const executedAt = mockNow().toISOString()
  const executedDate = kstDate(executedAt)
  const series = getPriceSeries(meta.ticker)
  const price = (series.findLast((p) => p.price_date <= executedDate) ?? series[0]).close_price
  const existing = store.holdings.get(meta.ticker)

  if (side === 'buy') {
    const cost = price * quantity
    if (cost > store.cashBalance) {
      throw new ApiError(
        `잔고가 부족합니다(필요 ${Math.round(cost).toLocaleString('ko-KR')}원, 보유 ${store.cashBalance.toLocaleString('ko-KR')}원).`,
        400,
      )
    }
    store.cashBalance -= cost
    const newQuantity = (existing?.quantity ?? 0) + quantity
    const avg_price = existing ? (existing.quantity * existing.avg_price + quantity * price) / newQuantity : price
    store.holdings.set(meta.ticker, { quantity: newQuantity, avg_price })
  } else {
    if (!existing || existing.quantity < quantity) {
      throw new ApiError(`보유 수량이 부족합니다(매도 요청 ${quantity}주, 보유 ${existing?.quantity ?? 0}주).`, 400)
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
    asset_type: meta.assetType,
    side,
    quantity,
    price,
    amount: Math.round(price * quantity),
    executed_at: executedAt,
  })

  const after = store.holdings.get(meta.ticker)
  return {
    ticker: meta.ticker,
    company_name: meta.name,
    side,
    quantity,
    price,
    executed_at: executedAt,
    cash_balance: store.cashBalance,
    holding: after ? { quantity: after.quantity, avg_price: after.avg_price } : null,
    asset_type: meta.assetType,
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
    throw new ApiError('보유 종목이 없습니다. 모의투자 주문 후 진단할 수 있어요.', 400)
  }

  const holdings: DiagnosisHolding[] = []
  const marketEval: Record<string, number> = {}
  // stockEvalAmount: 보유 종목 전체(ETF 포함) 평가금액 합 - herfindahl_index/total_asset의 분모다.
  // stocksOnlyEvalAmount: ETF를 뺀 주식만의 평가금액 합 - market_weights_pct의 분모다(backend/app/diagnosis.py
  // 의 stock_eval/stocks_only_eval과 같은 구분. 이름은 헷갈리지만 실제 백엔드도 이렇게 나뉘어 있다).
  let stockEvalAmount = 0
  let stocksOnlyEvalAmount = 0
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
    // ETF는 시장 쏠림 계산에서 뺀다(backend/API.md 2026-10-14) - market도 "ETF"로 둔다(예전엔 "OTHER").
    const holdingMarket = meta.assetType === 'etf' ? 'ETF' : meta.market!
    if (meta.assetType === 'stock') {
      marketEval[holdingMarket] = (marketEval[holdingMarket] ?? 0) + eval_amount
      stocksOnlyEvalAmount += eval_amount
    }

    holdings.push({
      ticker: meta.ticker,
      company_name: meta.name,
      asset_type: meta.assetType,
      market: holdingMarket,
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
    marketWeightsPct[market] =
      stocksOnlyEvalAmount > 0 ? Math.round((amount / stocksOnlyEvalAmount) * 10000) / 100 : null
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
  // ETF를 하나라도 보유하면 FEW_HOLDINGS를 걸지 않는다(backend/API.md 2026-10-14) - ETF는 한 상품
  // 안에서 여러 자산에 나눠 투자해서 종목 수로 분산 정도를 보기 어렵다. 대신 strengths에 안내한다.
  const heldEtfCount = holdings.filter((h) => h.asset_type === 'etf').length
  let etfFewHoldingsNote: string | null = null
  if (holdings.length < 3) {
    if (heldEtfCount > 0) {
      etfFewHoldingsNote =
        `보유 종목은 ${holdings.length}개지만 ETF가 ${heldEtfCount}개 포함돼 있습니다. ETF는 한 상품 ` +
        '안에서 여러 자산에 나눠 투자해서 종목 수로 분산 정도를 보기는 어렵습니다.'
    } else {
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
  // LEVERAGED_INVERSE_ETF: 레버리지·인버스 ETF를 보유한 종목마다 1개(backend/API.md 2026-10-14).
  for (const h of holdings) {
    if (h.asset_type === 'etf' && isLeveragedOrInverseEtf(h.company_name)) {
      const kind = /인버스/.test(h.company_name) ? '인버스' : '레버리지'
      const weight = h.weight_pct ?? 0
      flags.push({
        code: 'LEVERAGED_INVERSE_ETF',
        message:
          `${h.company_name}${josaEunNeun(h.company_name)} ${kind} ETF로 총자산의 ${weight}%입니다. 하루 수익률을 기준으로 움직여 ` +
          '오래 보유하면 기초지수 수익률과 달라질 수 있습니다.',
        value: weight,
        threshold: 0,
        ticker: h.ticker,
        company_name: h.company_name,
        market: null,
      })
    }
  }

  const totalProfitLossPct = totalCost > 0 ? Math.round((totalProfitLoss / totalCost) * 10000) / 100 : null

  const summary =
    `현재 포트폴리오의 총자산은 ${Math.round(totalAsset).toLocaleString('ko-KR')}원이며, ` +
    `현금 비중은 ${cashWeightPct ?? '-'}%입니다. 보유 종목은 ${holdings.length}개이고 ` +
    `총 평가손익은 ${Math.round(totalProfitLoss).toLocaleString('ko-KR')}원(${totalProfitLossPct ?? '-'}%)입니다.`

  const strengths = [
    ...(flags.length === 0 ? [`상위 1종목 비중이 ${top1WeightPct ?? '-'}%로 한 종목에 크게 쏠려 있지 않습니다.`] : []),
    `${holdings.length}개 종목에 나눠 보유하고 있습니다.`,
    ...(etfFewHoldingsNote ? [etfFewHoldingsNote] : []),
  ]

  const risks =
    flags.length > 0 ? flags.map((f) => f.message) : ['규칙 기준으로 확인된 위험 항목은 없습니다.']

  const suggestions = [
    '종목별 뉴스와 공시를 리서치 탭에서 주기적으로 확인해볼 수 있습니다.',
    ...(flags.some((f) => f.code === 'CONCENTRATION_HIGH' || f.code === 'MARKET_SKEW')
      ? ['비중이 쏠린 종목·시장을 분산하는 것을 고려해볼 수 있습니다.']
      : []),
  ]

  const computed = {
    generated_at: new Date().toISOString(),
    source: 'rule_based' as const,
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
    // 2026-11-06 추가: 진단 당시 ETF 실질 노출 스냅샷(저장본이라 나중에 열어도 그때 값 그대로).
    look_through: toSnapshot(
      computeLookThrough({
        cash: store.cashBalance,
        holdings: [...store.holdings].map(([ticker, h]) => ({ ticker, quantity: h.quantity })),
        // 진단이 쓴 현재가(= 마지막 종가)로 평가한다(valuation_basis: diagnosis_prices)
        priceOf: (ticker) => {
          const last = getPriceSeries(ticker).at(-1)
          return last ? { price: last.close_price, date: last.price_date } : null
        },
        limit: 20,
        locale: store.preferences.locale,
        expectedDate: getPriceSeries('005930').at(-1)!.price_date,
        lagBusinessDays: 0,
        tradingDates: getPriceSeries('005930').map((p) => p.price_date),
      }),
    ),
  }

  // 성공한 진단은 이력에 저장한다(최근 50건, backend/API.md "진단 이력"). 실패 없이 매번 저장되므로
  // diagnosis_id/created_at은 항상 값이 있다(저장 실패를 흉내 내지는 않는다).
  const id = store.nextDiagnosisId++
  const created_at = new Date().toISOString()
  store.diagnosisHistory.push({ ...computed, id, created_at })
  if (store.diagnosisHistory.length > MAX_DIAGNOSIS_HISTORY) store.diagnosisHistory.shift()

  return { ...computed, diagnosis_id: id, created_at }
}

// ---------- 포트폴리오 진단 이력(2026-10-14 추가) ----------

function toHistoryItem(detail: DiagnosisDetail): DiagnosisHistoryItem {
  return {
    id: detail.id,
    created_at: detail.created_at!,
    source: detail.source,
    total_asset: detail.metrics.total_asset,
    holdings_count: detail.metrics.holding_count,
    flags: detail.flags.map(
      (f): DiagnosisFlagSummary => ({ code: f.code, ticker: f.ticker, company_name: f.company_name, market: f.market }),
    ),
    summary_preview: detail.summary.length > 80 ? detail.summary.slice(0, 80) : detail.summary,
  }
}

function findDiagnosis(store: UserStore, diagnosisId: number): DiagnosisDetail {
  const found = store.diagnosisHistory.find((d) => d.id === diagnosisId)
  if (!found) throw new ApiError(`diagnosis_id=${diagnosisId} 진단을 찾을 수 없습니다.`, 404)
  return found
}

// 같은 플래그인지는 (code, ticker, market)으로 가른다(backend/API.md) - BIG_LOSS는 종목마다,
// MARKET_SKEW는 시장마다 따로 신규/해소를 잡는다.
function flagKey(f: { code: string; ticker: string | null; market: string | null }): string {
  return `${f.code}|${f.ticker ?? ''}|${f.market ?? ''}`
}

function computeDiagnosisChanges(previous: DiagnosisDetail, current: DiagnosisDetail): DiagnosisChanges {
  const prevAsset = previous.metrics.total_asset
  const currAsset = current.metrics.total_asset
  const total_asset: DiagnosisTotalAssetChange = {
    previous: prevAsset,
    current: currAsset,
    change_amount: Math.round((currAsset - prevAsset) * 100) / 100,
    change_pct: prevAsset !== 0 ? Math.round(((currAsset - prevAsset) / prevAsset) * 10000) / 100 : null,
  }

  const prevByTicker = new Map(previous.holdings.map((h) => [h.ticker, h]))
  const currByTicker = new Map(current.holdings.map((h) => [h.ticker, h]))
  const tickers = new Set([...prevByTicker.keys(), ...currByTicker.keys()])

  const holdings: DiagnosisHoldingChange[] = []
  for (const ticker of tickers) {
    const p = prevByTicker.get(ticker)
    const c = currByTicker.get(ticker)
    const previous_weight_pct = p?.weight_pct ?? null
    const current_weight_pct = c?.weight_pct ?? null
    const weight_change_pp =
      previous_weight_pct != null && current_weight_pct != null
        ? Math.round((current_weight_pct - previous_weight_pct) * 100) / 100
        : current_weight_pct != null
          ? current_weight_pct // added: 이전을 0으로 봄
          : previous_weight_pct != null
            ? -previous_weight_pct // removed: -이전 비중
            : null
    holdings.push({
      ticker,
      company_name: (c ?? p)!.company_name,
      status: !p ? 'added' : !c ? 'removed' : 'kept',
      previous_weight_pct,
      current_weight_pct,
      weight_change_pp,
    })
  }
  holdings.sort((a, b) => Math.abs(b.weight_change_pp ?? 0) - Math.abs(a.weight_change_pp ?? 0))

  const prevFlags = new Map(previous.flags.map((f) => [flagKey(f), f]))
  const currFlags = new Map(current.flags.map((f) => [flagKey(f), f]))
  const added = [...currFlags.entries()].filter(([key]) => !prevFlags.has(key)).map(([, f]) => f)
  const resolved = [...prevFlags.entries()].filter(([key]) => !currFlags.has(key)).map(([, f]) => f)

  const returns_20d: DiagnosisReturnChange[] = []
  for (const ticker of tickers) {
    const p = prevByTicker.get(ticker)?.return_20d_pct
    const c = currByTicker.get(ticker)?.return_20d_pct
    if (p == null || c == null) continue
    returns_20d.push({
      ticker,
      company_name: (currByTicker.get(ticker) ?? prevByTicker.get(ticker))!.company_name,
      previous_return_20d_pct: p,
      current_return_20d_pct: c,
      change_pp: Math.round((c - p) * 100) / 100,
    })
  }
  returns_20d.sort((a, b) => Math.abs(b.change_pp ?? 0) - Math.abs(a.change_pp ?? 0))

  return { total_asset, holdings, flags: { added, resolved }, returns_20d }
}

async function getDiagnosisHistory(limit = 20, offset = 0): Promise<DiagnosisHistoryList> {
  await delay(150)
  const store = storeFor(currentUserId())
  const sorted = store.diagnosisHistory.slice().reverse() // 최신순
  return {
    total: store.diagnosisHistory.length,
    limit,
    offset,
    items: sorted.slice(offset, offset + limit).map(toHistoryItem),
  }
}

async function getDiagnosisDetail(diagnosisId: number): Promise<DiagnosisDetail> {
  await delay(150)
  return findDiagnosis(storeFor(currentUserId()), diagnosisId)
}

async function compareDiagnosis(diagnosisId: number, baseId?: number): Promise<DiagnosisCompareResponse> {
  await delay(150)
  const store = storeFor(currentUserId())
  const current = findDiagnosis(store, diagnosisId)

  let previous: DiagnosisDetail | undefined
  if (baseId != null) {
    previous = findDiagnosis(store, baseId)
  } else {
    // base를 안 주면 바로 이전 진단(created_at 기준, 같으면 id 기준)을 쓴다. 이력에 저장된 항목은
    // 우리가 직접 만들었으니(getDiagnosis) created_at이 항상 실제 문자열이다(! 로 단언).
    const currentCreatedAt = current.created_at!
    previous = store.diagnosisHistory
      .filter(
        (d) =>
          d.id !== current.id &&
          (d.created_at! < currentCreatedAt || (d.created_at === currentCreatedAt && d.id < current.id)),
      )
      .sort((a, b) => (a.created_at === b.created_at ? b.id - a.id : b.created_at!.localeCompare(a.created_at!)))[0]
  }

  const basis = {
    current_created_at: current.created_at!,
    current_generated_at: current.generated_at,
    previous_created_at: previous?.created_at ?? null,
    previous_generated_at: previous?.generated_at ?? null,
    // 저장 당시 값끼리 비교한다 - 현재 시세로 다시 계산하지 않는다(핸드오프 I18N_HISTORY 3-6절)
    recomputed: false,
    note: '두 진단은 각각 저장 당시의 보유 구성과 시세로 계산한 결과입니다. 기준 시점이 서로 다르고, 현재 시세로 다시 계산하지 않았습니다.',
  }
  if (!previous) {
    return {
      current: toHistoryItem(current),
      previous: null,
      changes: null,
      disclaimer: DISCLAIMER,
      basis,
      metric_changes: null,
      exposure_changes: null,
    }
  }
  return {
    current: toHistoryItem(current),
    previous: toHistoryItem(previous),
    changes: computeDiagnosisChanges(previous, current),
    disclaimer: DISCLAIMER,
    basis,
    metric_changes: computeMetricChanges(previous, current),
    exposure_changes: computeExposureChanges(previous.look_through, current.look_through),
  }
}

function metricChange(previous: number | null, current: number | null) {
  return { previous, current, change: previous != null && current != null ? Math.round((current - previous) * 10000) / 10000 : null }
}

function computeMetricChanges(previous: DiagnosisDetail, current: DiagnosisDetail) {
  const p = previous.metrics
  const c = current.metrics
  return {
    cash_balance: metricChange(p.cash_balance, c.cash_balance),
    cash_weight_pct: metricChange(p.cash_weight_pct, c.cash_weight_pct),
    stock_eval_amount: metricChange(p.stock_eval_amount, c.stock_eval_amount),
    holding_count: metricChange(p.holding_count, c.holding_count),
    top1_weight_pct: metricChange(p.top1_weight_pct, c.top1_weight_pct),
    top3_weight_pct: metricChange(p.top3_weight_pct, c.top3_weight_pct),
    herfindahl_index: metricChange(p.herfindahl_index, c.herfindahl_index),
    total_profit_loss_pct: metricChange(p.total_profit_loss_pct, c.total_profit_loss_pct),
  }
}

// 두 저장본 모두에 status ok인 실질 노출 스냅샷이 있을 때만(아니면 null). 기업은 각 저장본에 남긴 상위 20개끼리 비교한다.
function computeExposureChanges(
  previous: LookThroughSnapshot | null | undefined,
  current: LookThroughSnapshot | null | undefined,
): ExposureChanges | null {
  if (!previous || !current || previous.status !== 'ok' || current.status !== 'ok') return null
  const before = new Map(previous.companies.map((c) => [c.code ?? c.name, c]))
  const after = new Map(current.companies.map((c) => [c.code ?? c.name, c]))
  const keys = new Set([...before.keys(), ...after.keys()])
  const companies = [...keys]
    .map((key) => {
      const b = before.get(key)
      const a = after.get(key)
      const prevPct = b?.total_weight_pct ?? null
      const curPct = a?.total_weight_pct ?? null
      return {
        name: (a ?? b)!.name,
        code: (a ?? b)!.code,
        previous_total_weight_pct: prevPct,
        current_total_weight_pct: curPct,
        change_pct: Math.round(((curPct ?? 0) - (prevPct ?? 0)) * 100) / 100,
        previous_total_amount: b?.total_amount ?? null,
        current_total_amount: a?.total_amount ?? null,
        status: (b == null ? 'added' : a == null ? 'removed' : 'changed') as 'added' | 'removed' | 'changed',
      }
    })
    .sort((x, y) => Math.abs(y.change_pct) - Math.abs(x.change_pct))
  const summaryKeys = ['total_asset', 'via_etf_amount', 'unidentified_weight_pct', 'companies_weight_pct', 'direct_top1_weight_pct'] as const
  return {
    calc_version: { previous: previous.calc_version, current: current.calc_version },
    valuation_date: { previous: previous.valuation_date, current: current.valuation_date },
    summary: Object.fromEntries(summaryKeys.map((k) => [k, metricChange(previous.summary[k], current.summary[k])])),
    look_through_top1: { previous: previous.summary.look_through_top1, current: current.summary.look_through_top1 },
    companies,
    note_code: 'stored_snapshots',
    companies_compared: 'top_saved',
  }
}

async function deleteDiagnosis(diagnosisId: number): Promise<void> {
  await delay(150)
  const store = storeFor(currentUserId())
  const index = store.diagnosisHistory.findIndex((d) => d.id === diagnosisId)
  if (index === -1) throw new ApiError(`diagnosis_id=${diagnosisId} 진단을 찾을 수 없습니다.`, 404)
  store.diagnosisHistory.splice(index, 1)
}

// 예전부터 있던 영역의 mock. 새 영역(성과·노출·브리핑·기록·ETF)은 mock-*.ts에 있고 mockApi.ts가 합친다.
export const baseMockApi: BaseApi = {
  signup,
  login,
  me,
  logout,
  getPreferences,
  updatePreferences,

  askResearch,
  listReports,
  getReport,
  getReportRecord,
  deleteReport,

  getRealtimePrice,
  getTrending,
  searchCompanies,
  getDisclaimer,
  getStatus,
  getEventReactions,
  getPriceHistory,

  listWatchlist,
  addWatchlistItem,
  removeWatchlistItem,
  getPortfolio,
  placeOrder,
  getTrades,
  resetPortfolio,
  getDiagnosis,
  getDiagnosisHistory,
  getDiagnosisDetail,
  compareDiagnosis,
  deleteDiagnosis,
}
