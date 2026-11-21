import { ApiError } from './errors'

// 화면 언어에 맞는 서버 오류 문장(2026-11-21). 영어 화면에서 서버의 한국어 detail("보유 수량이 부족합니다…")이 그대로 보였다.
// 규칙(detail 문장을 부분 문자열로 비교해 매핑하지 않는다):
// 1. 한국어 화면: 지금처럼 서버 detail을 그대로 쓴다(수량·금액까지 담긴 가장 정확한 문장). detail이 없으면 code 문구.
// 2. 다른 언어 화면 + 알려진 code: 그 언어의 code 문구.
// 3. 다른 언어 화면 + 모르는 code, 또는 code 없는 예전 응답인데 detail이 한국어: 그 언어의 상태 코드별 일반 문구.
// 4. 다른 언어 화면 + detail이 한국어가 아님(서버가 그 언어로 만든 문장): detail 그대로.
type Translate = (key: string, options?: { fallback?: string; vars?: Record<string, string | number> }) => string

const HANGUL_RE = /[가-힣]/

// 서버 code -> 화면 문구. 키는 전부 리터럴로 둔다(i18n coverage 테스트가 번역 함수 호출의 키를 찾아 번역 누락을 잡는다).
export function codeMessage(code: string | undefined, t: Translate): string | null {
  switch (code) {
    case 'INSUFFICIENT_HOLDINGS':
      return t('errors.code.insufficient_holdings', { fallback: '보유 수량이 부족합니다.' })
    case 'INSUFFICIENT_CASH':
      return t('errors.code.insufficient_cash', { fallback: '현금 잔고가 부족합니다.' })
    case 'INVALID_ORDER_QUANTITY':
      return t('errors.code.invalid_order_quantity', { fallback: '주문 수량이 올바르지 않습니다. 1주 이상의 정수로 입력해 주세요.' })
    case 'UNSUPPORTED_ORDER_TYPE':
      return t('errors.code.unsupported_order_type', { fallback: '지원하지 않는 주문 유형입니다. 매수 또는 매도만 할 수 있습니다.' })
    case 'INVALID_ORDER_REQUEST':
      return t('errors.code.invalid_order_request', { fallback: '주문 요청이 올바르지 않습니다. 입력값을 확인해 주세요.' })
    case 'STOCK_NOT_FOUND':
      return t('errors.code.stock_not_found', { fallback: '종목을 찾을 수 없습니다. 종목명이나 종목코드를 확인해 주세요.' })
    case 'PRICE_UNAVAILABLE':
      return t('errors.code.price_unavailable', { fallback: '지금은 가격을 확인할 수 없어 주문을 체결하지 못했습니다. 잠시 후 다시 시도해 주세요.' })
    case 'NOT_IN_WATCHLIST':
      return t('errors.code.not_in_watchlist', { fallback: '관심종목에 등록되어 있지 않은 종목입니다.' })
    case 'ALREADY_IN_WATCHLIST':
      return t('errors.code.already_in_watchlist', { fallback: '이미 관심종목에 등록된 종목입니다.' })
    case 'RESET_CONFIRM_REQUIRED':
      return t('errors.code.reset_confirm_required', { fallback: '초기화를 확인해야 진행할 수 있습니다.' })
    case 'AUTHENTICATION_REQUIRED':
      return t('errors.code.authentication_required', { fallback: '인증이 필요합니다. 다시 로그인해 주세요.' })
    case 'INVALID_CREDENTIALS':
      return t('errors.code.invalid_credentials', { fallback: '아이디 또는 비밀번호가 올바르지 않습니다.' })
    default:
      return null
  }
}

// 상태 코드별 일반 문구(백엔드 리소스 errors.*에 4개 언어가 있다)
export function statusMessage(status: number, t: Translate): string {
  switch (status) {
    case 400:
      return t('errors.400')
    case 401:
      return t('errors.401')
    case 404:
      return t('errors.404')
    case 409:
      return t('errors.status_409', { fallback: '이미 있는 데이터와 충돌해 처리하지 못했습니다.' })
    case 413:
      return t('errors.413')
    case 422:
      return t('errors.422')
    case 429:
      return t('errors.429')
    case 503:
      return t('errors.503')
    default:
      return t('errors.500')
  }
}

// 서버 오류(ApiError)를 화면 언어 문장으로. 클라이언트가 만든 오류(시간 초과 등)는 호출측(ErrorNotice)이 따로 다룬다.
export function localizedServerMessage(err: ApiError, t: Translate, locale: string): string {
  const detail = err.serverMessage ?? err.message
  if (locale === 'ko-KR') return detail || codeMessage(err.code, t) || statusMessage(err.status, t)
  const byCode = codeMessage(err.code, t)
  if (byCode) return byCode
  if (!detail || HANGUL_RE.test(detail)) return statusMessage(err.status, t)
  return detail
}

// 폼 오류 한 줄(주문·관심종목·초기화 등). ApiError면 위 규칙, 네트워크 끊김(fetch TypeError)이면 network 문구, 그 밖에는 fallback.
export function errorText(err: unknown, t: Translate, locale: string, fallback: string): string {
  if (err instanceof ApiError) {
    if (err.clientCode === 'timeout') return t('errors.client_timeout', { fallback: err.message || fallback })
    if (err.clientCode === 'schema') return t('errors.client_schema', { fallback: err.message || fallback })
    if (err.clientCode === 'request_failed') {
      return t('errors.client_request_failed', { fallback: err.message || fallback, vars: { status: err.status } })
    }
    return localizedServerMessage(err, t, locale)
  }
  if (err instanceof TypeError) return t('errors.network')
  if (err instanceof Error && err.message && (locale === 'ko-KR' || !HANGUL_RE.test(err.message))) return err.message
  return fallback
}
