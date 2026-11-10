// 메일 링크(이메일 인증·비밀번호 재설정)의 토큰은 쿼리가 아니라 주소의 fragment(#token=...)로 온다. fragment는 서버 로그와
// Referer에 남지 않기 때문이다(backend/API.md). 화면은 읽자마자 주소창에서 지워서 뒤로 가기·화면 공유로 새지 않게 한다.
//
// React StrictMode(개발)는 상태 초기화 함수를 두 번 부른다. 첫 호출이 주소를 지우면 둘째 호출은 빈 fragment를 보게 되므로,
// 읽은 값을 같은 경로에서는 다시 돌려준다.
let consumed: { path: string; token: string | null } | null = null

export function readHashToken(): string | null {
  const path = window.location.pathname
  const hash = window.location.hash
  if (hash.length > 1) {
    const token = new URLSearchParams(hash.slice(1)).get('token')
    window.history.replaceState(window.history.state, '', path + window.location.search)
    consumed = { path, token }
    return token
  }
  return consumed?.path === path ? consumed.token : null
}

// 테스트용
export function resetHashTokenCache(): void {
  consumed = null
}
