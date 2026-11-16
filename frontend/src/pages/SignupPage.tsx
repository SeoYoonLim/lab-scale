import { useState } from 'react'
import type { FormEvent } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { useAuth } from '../auth/useAuth'
import AuthBrand from '../components/AuthBrand'
import AuthShell from '../components/AuthShell'
import { useLocale } from '../i18n/useLocale'

export default function SignupPage() {
  const { signup } = useAuth()
  const { t } = useLocale()
  const navigate = useNavigate()
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
      await signup(username, password)
      navigate('/', { replace: true })
    } catch (err) {
      setError(err instanceof Error ? err.message : t('auth.signup_failed', { fallback: '회원가입에 실패했어요.' }))
    } finally {
      setPending(false)
    }
  }

  return (
    <AuthShell>
      <form className="card auth-card" onSubmit={handleSubmit}>
        <AuthBrand />
        <h1 className="auth-title">{t('auth.signup', { fallback: '회원가입' })}</h1>
        <div className="auth-field">
          <label htmlFor="signup-username">{t('auth.username', { fallback: '아이디' })}</label>
          <input
            id="signup-username"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            autoComplete="username"
            aria-describedby="signup-username-help"
          />
          {/* 규칙은 placeholder가 아니라 화면에 남는 글로 둔다(입력하면 placeholder는 사라진다) */}
          <span id="signup-username-help" className="muted small">
            {t('auth.username_placeholder', { fallback: '영문 소문자/숫자/밑줄, 3~20자' })}
          </span>
        </div>
        <div className="auth-field">
          <label htmlFor="signup-password">{t('auth.password', { fallback: '비밀번호' })}</label>
          <input
            id="signup-password"
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="new-password"
            aria-describedby="signup-password-help"
          />
          <span id="signup-password-help" className="muted small">
            {t('auth.password_placeholder', { fallback: '8~128자 (흔한 비밀번호·아이디가 든 비밀번호는 안 돼요)' })}
          </span>
        </div>
        {error && <p className="notice notice-error" role="alert">{error}</p>}
        <button
          type="submit"
          className="send auth-submit"
          disabled={pending || !username.trim() || password.length < 8}
        >
          {pending ? t('auth.signing_up', { fallback: '가입 중…' }) : t('auth.signup', { fallback: '회원가입' })}
        </button>
        <p className="muted small auth-switch">
          {t('auth.have_account', { fallback: '이미 계정이 있으신가요?' })} <Link to="/login">{t('nav.login', { fallback: '로그인' })}</Link>
        </p>
      </form>
    </AuthShell>
  )
}
