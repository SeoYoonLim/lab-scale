const KEY = 'device_id'

// 로그인이 없는 서비스라, 관심종목·모의투자는 이 값(X-Device-Id)으로 사용자를 구분한다.
// 인증이 아니라 단순 구분자다 — 값을 잃어버리면(localStorage 초기화 등) 그 상태도 못 찾는다.
// (backend/API.md "디바이스ID" 참고)
export function getDeviceId(): string {
  try {
    const existing = localStorage.getItem(KEY)
    if (existing) return existing
    const id = crypto.randomUUID()
    localStorage.setItem(KEY, id)
    return id
  } catch {
    // 프라이빗 모드 등으로 localStorage를 못 쓰면 이번 세션 동안만 쓰는 값으로 대체한다.
    return `session-${Math.random().toString(36).slice(2)}`
  }
}
