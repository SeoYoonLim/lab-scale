// 2026-10-07부터 로그인(JWT)으로 바뀌면서 X-Device-Id는 더 이상 안 쓴다(deviceId.ts 삭제).
// 토큰은 localStorage에 저장하고, 인증 필요 API를 부를 때마다 Authorization: Bearer 헤더로 보낸다.

const KEY = 'access_token'

// 어떤 요청이든 401을 받으면(토큰 없음/만료/변조) http.ts가 이 이벤트를 쏜다.
// AuthContext가 이걸 듣고 로그인 상태를 비워서, 보호된 화면이 로그인 화면으로 돌려보낸다.
export const AUTH_EXPIRED_EVENT = 'auth:expired'

// localStorage가 막혀 있으면(프라이빗 모드 등) 저장은 못 해도 이 메모리 값으로 최소한 이번 탭 세션
// 동안은 로그인 상태가 유지된다 - 안 그러면 로그인은 성공한 것처럼 보이는데 저장이 실제로는 안 돼서
// 바로 다음 요청에서 401이 난다. memoryToken을 항상 유일한 출처로 쓴다(모듈 로드 시 localStorage에서
// 한 번만 읽어 시드한다) - getToken()이 매번 localStorage를 다시 읽으면, 기존 토큰 A가 저장된 채로
// setToken(B)의 "쓰기"만 실패했을 때 여전히 옛 토큰 A를 반환하는 문제가 생긴다.
let memoryToken: string | null = ((): string | null => {
  try {
    return localStorage.getItem(KEY)
  } catch {
    return null
  }
})()

export function getToken(): string | null {
  return memoryToken
}

export function setToken(token: string): void {
  memoryToken = token
  try {
    localStorage.setItem(KEY, token)
  } catch {
    // no-op - 위 memoryToken으로 이번 세션은 동작한다.
  }
}

export function clearToken(): void {
  memoryToken = null
  try {
    localStorage.removeItem(KEY)
  } catch {
    // no-op
  }
}
