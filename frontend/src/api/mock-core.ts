import type { AssetType, Company } from '../types'
import { ApiError } from './errors'
import type { DateGroup } from './contracts/common'

// 화면 개발용 샘플 데이터. 실제 시세/뉴스/공시/사용자가 아니다.

export const DISCLAIMER =
  '이 서비스는 학습·시연용 모의투자와 AI 리서치입니다. 제공되는 분석과 의견은 투자 권유나 자문이 아니며, ' +
  '투자 판단과 그에 따른 손익의 책임은 본인에게 있습니다. AI 응답에는 오류가 있을 수 있습니다.'

export interface CompanyMeta {
  ticker: string
  name: string
  // ETF는 market이 null(backend/API.md "종목 유형: 주식과 ETF")
  market: 'KOSPI' | 'KOSDAQ' | null
  assetType: AssetType
}

// 백엔드 DB의 sector 컬럼은 2026-10-07 기준 실제로 비어 있다(README "구현된 API" 참고) — mock도 null로 맞춘다.
export const COMPANY_META: CompanyMeta[] = [
  { ticker: '005930', name: '삼성전자', market: 'KOSPI', assetType: 'stock' },
  { ticker: '000660', name: 'SK하이닉스', market: 'KOSPI', assetType: 'stock' },
  { ticker: '035420', name: 'NAVER', market: 'KOSPI', assetType: 'stock' },
  { ticker: '035720', name: '카카오', market: 'KOSPI', assetType: 'stock' },
  { ticker: '005380', name: '현대차', market: 'KOSPI', assetType: 'stock' },
  { ticker: '373220', name: 'LG에너지솔루션', market: 'KOSPI', assetType: 'stock' },
  { ticker: '247540', name: '에코프로비엠', market: 'KOSDAQ', assetType: 'stock' },
  // ETF(2026-10-14 추가). 실제 API.md 예시와 같은 종목코드/이름을 쓴다 - 레버리지·인버스는
  // LEVERAGED_INVERSE_ETF 플래그 테스트용.
  { ticker: '069500', name: 'KODEX 200', market: null, assetType: 'etf' },
  { ticker: '360750', name: 'TIGER 미국S&P500', market: null, assetType: 'etf' },
  { ticker: '122630', name: 'KODEX 레버리지', market: null, assetType: 'etf' },
  { ticker: '114800', name: 'KODEX 인버스', market: null, assetType: 'etf' },
]

export const BASE_PRICE: Record<string, number> = {
  '005930': 70000,
  '000660': 180000,
  '035420': 200000,
  '035720': 45000,
  '005380': 230000,
  '373220': 380000,
  '247540': 130000,
  '069500': 36000,
  '360750': 19000,
  '122630': 83000,
  '114800': 5500,
}

// backend/API.md: 레버리지·인버스는 ETF 이름으로 판별한다(LEVERAGED_INVERSE_ETF 플래그).
export function isLeveragedOrInverseEtf(name: string): boolean {
  return /레버리지|인버스|2X/i.test(name)
}

// 종목명에 "은/는"을 붙인다(한글 마지막 글자의 받침 유무로 판단). 실제 백엔드처럼 읽는 소리까지
// 완벽히 따지지는 않지만("200"처럼 숫자로 끝나는 이름은 받침 있는 쪽으로 둔다), 적어도 한글로 끝나는
// 흔한 ETF 이름("KODEX 레버리지" 등)에서 "레버리지은"처럼 틀리진 않는다.
export function josaEunNeun(name: string): string {
  const last = name.trim().at(-1) ?? ''
  const code = last.codePointAt(0) ?? 0
  if (code >= 0xac00 && code <= 0xd7a3) {
    return (code - 0xac00) % 28 === 0 ? '는' : '은'
  }
  return '은'
}

export const DISCLOSURE_TYPES = [
  '분기보고서',
  '주요사항보고서(자기주식취득결정)',
  '주식등의대량보유상황보고서',
  '기업설명회(IR)개최',
  '영업(잠정)실적(공정공시)',
]

export const delay = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms))

export function notFoundReport(reportId: number): ApiError {
  return new ApiError(`report_id=${reportId} 리포트를 찾을 수 없습니다.`, 404)
}

export function companyNotFound(input: string): ApiError {
  return new ApiError(`'${input}' 종목을 찾지 못했습니다. company 테이블에 등록된 종목명 또는 종목코드를 지정해주세요.`, 404)
}

export function unauthorized(): ApiError {
  return new ApiError('인증이 필요합니다. 다시 로그인해주세요.', 401)
}

// 같은 종목은 항상 같은 값이 나오도록 시드 기반 난수를 쓴다.
export function mulberry32(seed: number) {
  let a = seed
  return () => {
    a |= 0
    a = (a + 0x6d2b79f5) | 0
    let t = Math.imul(a ^ (a >>> 15), 1 | a)
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

export function toDateString(d: Date) {
  const y = d.getFullYear()
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  return `${y}-${m}-${day}`
}

export interface PriceSeriesPoint {
  price_date: string
  close_price: number
  volume: number | null
  change_pct: number | null
}

export const priceCache = new Map<string, PriceSeriesPoint[]>()

// 오늘까지 90거래일(주말 제외) 치 주가를 만든다. 백엔드 대응 API가 없어 이 파일 안에서만 쓰는 내부 모델이다.
export function getPriceSeries(ticker: string): PriceSeriesPoint[] {
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

export function findCompanyMeta(input: string): CompanyMeta | undefined {
  const needle = input.trim().toLowerCase()
  return COMPANY_META.find((c) => c.ticker === input || c.name.toLowerCase() === needle)
}

export function companyView(meta: CompanyMeta): Company {
  const latest = getPriceSeries(meta.ticker).at(-1)
  return {
    ticker: meta.ticker,
    name: meta.name,
    market: meta.market,
    sector: null,
    asset_type: meta.assetType,
    latest_close: latest?.close_price ?? null,
    change_pct: latest?.change_pct ?? null,
  }
}

export function makeNews(name: string, limit: number): { title: string; url: string }[] {
  return Array.from({ length: limit }, (_, i) => ({
    title: `[샘플] ${name} 관련 뉴스 ${i + 1}`,
    url: `https://search.naver.com/search.naver?where=news&query=${encodeURIComponent(name)}`,
  }))
}

export function makeDisclosures(name: string, limit: number): { title: string; url: string }[] {
  return Array.from({ length: limit }, (_, i) => ({
    title: `[샘플] ${name} ${DISCLOSURE_TYPES[i % DISCLOSURE_TYPES.length]}`,
    url: 'https://dart.fss.or.kr',
  }))
}


// ---------- 개발용 시계·데이터 지연 훅 ----------
// 실제 백엔드에서는 "오늘 체결한 주문은 다음 거래일 종가가 수집되기 전까지 pending_only"이고 시세가 늦으면 지연 경고가
// 붙는다(성과·노출·브리핑). 그런 화면 상태를 mock으로도 볼 수 있게, 브라우저 콘솔(또는 Playwright)에서 시계를 옮기거나
// 데이터가 며칠 늦은 상태를 흉내 낼 수 있는 작은 훅이다. localStorage에만 저장되고 개발 모드에서만 window에 붙는다.
//   __mockClock.setOffsetDays(-40)   // 지금부터 주문을 넣으면 40일 전에 체결한 것으로 기록된다(성과 곡선을 보려고)
//   __mockClock.setDataLagDays(3)    // 성과·노출·브리핑의 시세가 3영업일 늦은 상태
const CLOCK_KEY = 'mock_clock_offset_days'
const LAG_KEY = 'mock_data_lag_days'

function readNumber(key: string): number {
  try {
    const n = Number(localStorage.getItem(key))
    return Number.isFinite(n) ? n : 0
  } catch {
    return 0
  }
}

function writeNumber(key: string, value: number): void {
  try {
    if (value === 0) localStorage.removeItem(key)
    else localStorage.setItem(key, String(value))
  } catch {
    // 저장이 막혀도 훅이 안 먹을 뿐 앱은 그대로 동작한다.
  }
}

export function mockNow(): Date {
  return new Date(Date.now() + readNumber(CLOCK_KEY) * 86_400_000)
}

export function mockDataLagDays(): number {
  return Math.max(0, Math.floor(readNumber(LAG_KEY)))
}

export function setMockClockOffsetDays(days: number): void {
  writeNumber(CLOCK_KEY, days)
}

export function setMockDataLagDays(days: number): void {
  writeNumber(LAG_KEY, days)
}

if (import.meta.env.DEV && typeof window !== 'undefined') {
  ;(window as unknown as Record<string, unknown>).__mockClock = {
    setOffsetDays: setMockClockOffsetDays,
    setDataLagDays: setMockDataLagDays,
  }
}

// 지연 훅이 걸려 있으면 마지막 N거래일을 뺀 시세만 "수집된 것"으로 본다(성과·노출·브리핑 mock 전용).
export function collectedSeries(ticker: string): PriceSeriesPoint[] {
  const series = getPriceSeries(ticker)
  const lag = mockDataLagDays()
  return lag > 0 ? series.slice(0, Math.max(1, series.length - lag)) : series
}

// "기대 거래일"(오늘 이하 가장 최근 거래일)은 지연이 없는 시세의 마지막 날짜다.
export function expectedTradingDate(): string {
  return getPriceSeries('005930').at(-1)!.price_date
}

// 날짜 문자열(YYYY-MM-DD) -> 한국 시간 기준 날짜. ISO 시각을 서울 날짜로 바꾼다.
export function kstDate(iso: string): string {
  return new Date(iso).toLocaleDateString('sv-SE', { timeZone: 'Asia/Seoul' })
}

// ---------- 시간대·날짜 그룹 (기록·브리핑 mock 공용) ----------
export const DEFAULT_TIMEZONE = 'Asia/Seoul'

export function isValidTimezone(name: string): boolean {
  try {
    new Intl.DateTimeFormat('en-US', { timeZone: name })
    return true
  } catch {
    return false
  }
}

// ISO 시각을 그 시간대의 날짜(YYYY-MM-DD)와 시각(HH:MM)으로 바꾼다. 화면에는 created_at(UTC) 대신 이 값을 쓴다.
export function localParts(iso: string, timeZone: string): { date: string; time: string } {
  const date = new Date(iso)
  return {
    date: date.toLocaleDateString('sv-SE', { timeZone }),
    time: date.toLocaleTimeString('en-GB', { timeZone, hour: '2-digit', minute: '2-digit', hour12: false }),
  }
}

// "그 시간대의 오늘" 기준 달력 날짜 차이로 묶는다(오늘 0, 어제 1, 2~6, 7~29, 30 이상).
export function dateGroupOf(localDate: string, timeZone: string, now: Date = new Date()): DateGroup {
  const today = now.toLocaleDateString('sv-SE', { timeZone })
  const diffDays = Math.round((Date.parse(today) - Date.parse(localDate)) / 86_400_000)
  if (diffDays <= 0) return 'today'
  if (diffDays === 1) return 'yesterday'
  if (diffDays < 7) return 'last_7_days'
  if (diffDays < 30) return 'last_30_days'
  return 'older'
}


// 지수(코스피·코스닥·원달러) 같은 가짜 시계열. 시드가 같으면 같은 값이고, 길이가 달라도 앞부분은 같다.
export function syntheticIndexCloses(base: number, seed: number, length: number, volatility = 0.03): number[] {
  const rand = mulberry32(seed)
  let value = base
  const out: number[] = []
  for (let i = 0; i < length; i += 1) {
    value *= 1 + (rand() - 0.5) * volatility
    out.push(Math.round(value * 100) / 100)
  }
  return out
}
