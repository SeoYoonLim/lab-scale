import type { ReactNode } from 'react'
import { Navigate, useLocation } from 'react-router-dom'
import { useAuth } from '../auth/useAuth'

// 리서치/기록/포트폴리오처럼 로그인이 필요한 화면을 감싼다. 로그인 안 돼 있으면 /login으로 보내고,
// 로그인 뒤 원래 가려던 곳(경로 + state, 예: CompanyPage가 넘긴 리서치 prefill)으로 돌아올 수 있게
// location 전체를 넘긴다.
export default function RequireAuth({ children }: { children: ReactNode }) {
  const { user, loading, initError, retryInit } = useAuth()
  const location = useLocation()

  if (loading) {
    return (
      <div className="container page">
        <p className="muted">불러오는 중…</p>
      </div>
    )
  }

  // 로그인 확인이 네트워크/서버 오류로 실패한 경우: 토큰이 무효하다는 뜻이 아니므로 로그인 화면으로
  // 쫓아내지 않고 재시도를 안내한다(401이면 initError가 비어 있고 user===null로만 떨어진다).
  if (initError && !user) {
    return (
      <div className="container page">
        <p className="notice notice-error">{initError}</p>
        <button type="button" className="send" onClick={retryInit}>
          다시 시도
        </button>
      </div>
    )
  }

  if (!user) {
    return <Navigate to="/login" replace state={{ from: location.pathname, state: location.state }} />
  }

  return <>{children}</>
}
