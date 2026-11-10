export function formatPrice(value: number) {
  return value.toLocaleString('ko-KR')
}

export function formatVolume(value: number | null) {
  return value == null ? '-' : value.toLocaleString('ko-KR')
}

export function formatPct(value: number | null) {
  if (value == null) return '-'
  const sign = value > 0 ? '+' : ''
  return `${sign}${value.toFixed(2)}%`
}

// 한국 주식 관례: 상승 = 빨강(up), 하락 = 파랑(down)
export function trendClass(value: number | null) {
  if (value == null || value === 0) return 'flat'
  return value > 0 ? 'up' : 'down'
}

// YYYY-MM-DD 또는 ISO 8601 문자열을 한국 시간 기준 YYYY.MM.DD 로 바꾼다.
export function formatDate(value: string | null) {
  if (!value) return '-'
  if (value.length === 10) return value.replaceAll('-', '.')
  return new Date(value)
    .toLocaleDateString('sv-SE', { timeZone: 'Asia/Seoul' })
    .replaceAll('-', '.')
}

// ISO 8601 문자열을 한국 시간 기준 HH:MM 으로. 같은 날 여러 건 올라오는 리서치 항목 구분용.
export function formatTime(value: string) {
  return new Date(value).toLocaleTimeString('ko-KR', {
    hour: '2-digit',
    minute: '2-digit',
    timeZone: 'Asia/Seoul',
  })
}

// 시각(ISO 8601)을 사용자 시간대·언어로 "날짜 + 시각"으로 보여준다. created_at(UTC)·generated_at(KST)처럼 오프셋이
// 섞여 있어도 둘 다 Date로 정확히 읽히므로 문자열을 잘라 쓰지 않는다(핸드오프 15-3절).
export function formatDateTime(value: string | null, timeZone: string, locale: string): string {
  if (!value) return '-'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '-'
  return new Intl.DateTimeFormat(locale, { dateStyle: 'medium', timeStyle: 'short', timeZone }).format(date)
}

// 뉴스·공시가 KST(+09:00)로 올 때의 한국 날짜(YYYY-MM-DD). 공시는 날짜만 아는 값이라 T00:00:00+09:00인데,
// 오프셋을 무시하고 앞 10자를 쓰거나 UTC로 바꾸면 하루가 밀릴 수 있어서 항상 서울 시간대로 읽는다.
export function kstDateOf(value: string | null): string | null {
  if (!value) return null
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return null
  return date.toLocaleDateString('sv-SE', { timeZone: 'Asia/Seoul' })
}

// 거래일·기준일("2026-11-03")은 시간대가 없는 한국 거래일이라 Date로 바꾸지 않고 그대로 보여준다(UTC 자정으로 읽혀 미국
// 시간대에서 하루 이르게 보이는 문제). 점 표기는 기존 화면(formatDate)과 맞춘다.
export function formatTradingDate(value: string | null | undefined): string {
  return value ? value.replaceAll('-', '.') : '-'
}

// 금액(원). 숫자는 계산하지 않고 그대로 쓴다(핸드오프 0절 4번) - 모양만 잡는다. null은 0이 아니라 "—"다.
export function formatWon(value: number | null | undefined): string {
  return value == null ? '—' : `${Math.round(value).toLocaleString('ko-KR')}원`
}

// 퍼센트 값(-3.48 = -3.48%). null이면 "—". 부호는 상승/하락 색과 함께 읽히게 항상 붙인다.
export function formatSignedPct(value: number | null | undefined, digits = 2): string {
  if (value == null) return '—'
  return `${value > 0 ? '+' : ''}${value.toFixed(digits)}%`
}

// %포인트(초과수익률 등)
export function formatSignedPp(value: number | null | undefined, digits = 2): string {
  if (value == null) return '—'
  return `${value > 0 ? '+' : ''}${value.toFixed(digits)}%p`
}

// 비중처럼 부호 없는 퍼센트
export function formatPlainPct(value: number | null | undefined, digits = 2): string {
  return value == null ? '—' : `${value.toFixed(digits)}%`
}
