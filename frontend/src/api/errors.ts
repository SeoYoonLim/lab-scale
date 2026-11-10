// http.ts/mock.ts 둘 다 던진다. 상태 코드가 필요한 호출부(예: 409를 "이미 있음" 안내로 보여주는 경우)가
// 메시지 문자열 대신 err.status로 분기할 수 있게 한다.
// 서버가 문장을 주지 않아 클라이언트가 직접 만든 오류의 종류. 화면(ErrorNotice)이 이 코드로 화면 언어에 맞는 문장을 보여준다.
export type ClientErrorCode = 'timeout' | 'schema' | 'request_failed'

export class ApiError extends Error {
  readonly status: number
  // 429(요청 한도)일 때 응답 헤더 Retry-After(초). 없으면 undefined. "n초 뒤 다시"를 화면에 보여주는 데 쓴다.
  readonly retryAfterSec?: number
  // 클라이언트가 만든 오류(시간 초과·응답 형식 오류·본문 없는 실패)면 그 종류. 서버가 준 문장이면 undefined.
  readonly clientCode?: ClientErrorCode
  // 429에서 "n초 뒤 다시"를 덧붙이기 전의 서버 문장
  readonly serverMessage?: string

  constructor(message: string, status: number, retryAfterSec?: number, clientCode?: ClientErrorCode, serverMessage?: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.retryAfterSec = retryAfterSec
    this.clientCode = clientCode
    this.serverMessage = serverMessage
  }
}

// 화면마다 같은 상황(로그인 필요/없음/한도/서버 장애)을 같은 말로 안내하게 한 곳에 모은 분류.
// backend/docs/FRONTEND_INTEGRATION_HANDOFF.md 6절의 "화면 처리" 열과 같다.
export type ErrorKind = 'auth' | 'not_found' | 'conflict' | 'validation' | 'rate_limit' | 'timeout' | 'server' | 'unknown'

export function classifyError(err: unknown): ErrorKind {
  if (!(err instanceof ApiError)) return 'unknown'
  switch (err.status) {
    case 0:
      return 'timeout'
    case 401:
      return 'auth'
    case 404:
      return 'not_found'
    case 409:
      return 'conflict'
    case 422:
      return 'validation'
    case 429:
      return 'rate_limit'
    case 500:
    case 502:
    case 503:
      return 'server'
    default:
      return 'unknown'
  }
}

// 서버가 준 detail(한국어 문장)이 있으면 그대로가 가장 정확하다. 그래도 "다시 시도하면 되는 오류"인지는
// 화면이 알아야 재시도 버튼을 보여줄 수 있다.
export function isRetryable(err: unknown): boolean {
  const kind = classifyError(err)
  return kind === 'timeout' || kind === 'server' || kind === 'rate_limit' || kind === 'unknown'
}
