import { useState } from 'react'
import type { FormEvent } from 'react'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import { useAuth } from '../auth/useAuth'

export default function LoginPage() {
  const { login } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    if (pending) return
    setPending(true)
    setError(null)
    try {
      await login(username, password)
      // RequireAuth가 쫓아낼 때 넘긴 { from, state } - 원래 가려던 경로와 그 경로의 state(예: CompanyPage가
      // 넘긴 리서치 prefill)를 그대로 복원한다. 직접 /login으로 왔으면 이 state가 없다.
      const nav = location.state as { from?: string; state?: unknown } | null
      navigate(nav?.from ?? '/', { replace: true, state: nav?.state ?? null })
    } catch (err) {
      setError(err instanceof Error ? err.message : '로그인에 실패했어요.')
    } finally {
      setPending(false)
    }
  }

  return (
    <div className="auth-page">
      <form className="card auth-card" onSubmit={handleSubmit}>
        <h1 className="auth-title">로그인</h1>
        <label className="auth-field">
          <span>아이디</span>
          <input value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="username" />
        </label>
        <label className="auth-field">
          <span>비밀번호</span>
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="current-password"
          />
        </label>
        {error && <p className="notice notice-error">{error}</p>}
        <button type="submit" className="send auth-submit" disabled={pending || !username.trim() || !password}>
          {pending ? '로그인 중…' : '로그인'}
        </button>
        <p className="muted small auth-switch">
          계정이 없으신가요? <Link to="/signup">회원가입</Link>
        </p>
      </form>
    </div>
  )
}
