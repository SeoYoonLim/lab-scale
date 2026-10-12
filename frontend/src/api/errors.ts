// http.ts/mock.ts 둘 다 던진다. 상태 코드가 필요한 호출부(예: 409를 "이미 있음" 안내로 보여주는 경우)가
// 메시지 문자열 대신 err.status로 분기할 수 있게 한다.
export class ApiError extends Error {
  readonly status: number

  constructor(message: string, status: number) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}
