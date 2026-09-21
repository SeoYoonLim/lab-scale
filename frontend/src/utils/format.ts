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
