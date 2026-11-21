import { useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import { useAuth } from '../auth/useAuth'
import AuthBrand from '../components/AuthBrand'
import AuthShell from '../components/AuthShell'
import { useLocale } from '../i18n/useLocale'
import { errorText } from '../api/errorMessages'

export default function LoginPage() {
  const { login, notice } = useAuth()
  const { t, locale } = useLocale()
  const navigate = useNavigate()
  const location = useLocation()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const usernameRef = useRef<HTMLInputElement>(null)
  const passwordRef = useRef<HTMLInputElement>(null)

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    if (pending) return
    // 버튼을 끄는 대신 여기서 안내한다(꺼진 버튼은 키보드로 닿지 않아 이유를 알 수 없다)
    if (!username.trim() || !password) {
      setError(t('auth.fields_required', { fallback: '아이디와 비밀번호를 입력해주세요.' }))
      ;(username.trim() ? passwordRef : usernameRef).current?.focus()
      return
    }
    setPending(true)
    setError(null)
    try {
      await login(username, password)
      // RequireAuth가 쫓아낼 때 넘긴 { from, state } - 원래 가려던 경로와 그 경로의 state(예: CompanyPage가
      // 넘긴 리서치 prefill)를 그대로 복원한다. 직접 /login으로 왔으면 이 state가 없다.
      const nav = location.state as { from?: string; state?: unknown } | null
      navigate(nav?.from ?? '/', { replace: true, state: nav?.state ?? null })
    } catch (err) {
      setError(errorText(err, t, locale, t('auth.login_failed', { fallback: '로그인에 실패했어요.' })))
    } finally {
      setPending(false)
    }
  }

  return (
    <AuthShell>
      <form className="card auth-card" onSubmit={handleSubmit} noValidate>
        <AuthBrand />
        <h1 className="auth-title">{t('nav.login', { fallback: '로그인' })}</h1>
        {notice && (
          <p className="notice order-result" role="status">
            {notice}
          </p>
        )}
        <label className="auth-field">
          <span>{t('auth.username', { fallback: '아이디' })}</span>
          <input
            ref={usernameRef}
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            autoComplete="username"
            required
            aria-invalid={error != null && !username.trim() ? true : undefined}
            aria-describedby={error ? 'login-error' : undefined}
          />
        </label>
        <label className="auth-field">
          <span>{t('auth.password', { fallback: '비밀번호' })}</span>
          <input
            ref={passwordRef}
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="current-password"
            required
            aria-invalid={error != null && !password ? true : undefined}
            aria-describedby={error ? 'login-error' : undefined}
          />
        </label>
        {error && <p id="login-error" className="notice notice-error" role="alert">{error}</p>}
        <button type="submit" className="send auth-submit" aria-disabled={pending || undefined}>
          {pending ? t('auth.logging_in', { fallback: '로그인 중…' }) : t('nav.login', { fallback: '로그인' })}
        </button>
        <p className="muted small auth-switch">
          {t('auth.no_account', { fallback: '계정이 없으신가요?' })} <Link to="/signup">{t('auth.signup', { fallback: '회원가입' })}</Link>
        </p>
        <p className="muted small auth-switch">
          <Link to="/find-username">{t('auth.find_username', { fallback: '아이디 찾기' })}</Link>
          {' · '}
          <Link to="/forgot-password">{t('auth.forgot_password', { fallback: '비밀번호 찾기' })}</Link>
        </p>
      </form>
    </AuthShell>
  )
}
