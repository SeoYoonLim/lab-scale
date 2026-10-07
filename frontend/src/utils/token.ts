// 2026-10-07부터 로그인(JWT)으로 바뀌면서 X-Device-Id는 더 이상 안 쓴다(deviceId.ts 삭제).
// 토큰은 localStorage에 저장하고, 인증 필요 API를 부를 때마다 Authorization: Bearer 헤더로 보낸다.

const KEY = 'access_token'

// 어떤 요청이든 401을 받으면(토큰 없음/만료/변조) http.ts가 이 이벤트를 쏜다.
// AuthContext가 이걸 듣고 로그인 상태를 비워서, 보호된 화면이 로그인 화면으로 돌려보낸다.
export const AUTH_EXPIRED_EVENT = 'auth:expired'

export function getToken(): string | null {
  try {
    return localStorage.getItem(KEY)
  } catch {
    return null
  }
}

export function setToken(token: string): void {
  try {
    localStorage.setItem(KEY, token)
  } catch {
    // 프라이빗 모드 등으로 저장을 못 해도 이번 세션 메모리 값(React state)으로는 동작한다.
  }
}

export function clearToken(): void {
  try {
    localStorage.removeItem(KEY)
  } catch {
    // no-op
  }
}
