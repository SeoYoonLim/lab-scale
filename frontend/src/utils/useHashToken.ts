import { useEffect, useState } from 'react'
import { readHashToken } from './hashToken'

// 메일 링크의 #token=을 읽는 훅. 같은 탭에서 주소의 fragment만 바뀌는 이동(다른 링크를 붙여넣거나 누르는 경우)은 화면이 새로
// 열리지 않아서 hashchange로 새 토큰을 받는다. 그러지 않으면 예전 토큰과 예전 결과를 그대로 보여준다.
// (replaceState로 주소를 지우는 것은 hashchange를 일으키지 않는다.)
export function useHashToken(): string | null {
  const [token, setToken] = useState(readHashToken)
  useEffect(() => {
    function onHashChange() {
      const next = readHashToken()
      if (next) setToken(next)
    }
    window.addEventListener('hashchange', onHashChange)
    return () => window.removeEventListener('hashchange', onHashChange)
  }, [])
  return token
}
