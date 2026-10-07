import type { ReactNode } from 'react'
import { Navigate, useLocation } from 'react-router-dom'
import { useAuth } from '../auth/AuthContext'

// 리서치/기록/포트폴리오처럼 로그인이 필요한 화면을 감싼다. 로그인 안 돼 있으면 /login으로 보내고,
// 로그인 뒤 원래 가려던 곳으로 돌아올 수 있게 from을 같이 넘긴다.
export default function RequireAuth({ children }: { children: ReactNode }) {
  const { user, loading } = useAuth()
  const location = useLocation()

  if (loading) {
    return (
      <div className="container page">
        <p className="muted">불러오는 중…</p>
      </div>
    )
  }

  if (!user) {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />
  }

  return <>{children}</>
}
