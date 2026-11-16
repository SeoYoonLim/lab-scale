import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { api } from '../../api'
import { looksLikeEmail } from '../../api/contracts/account'
import { queryKeys } from '../../api/queryKeys'
import MockMailbox from '../../components/MockMailbox'
import ErrorNotice from '../../components/ui/ErrorNotice'
import StatusBadge from '../../components/ui/StatusBadge'
import { useLocale } from '../../i18n/useLocale'
import LoadingNotice from '../../components/ui/LoadingNotice'

// 복구용 이메일(PUT /api/auth/email). 인증된 이메일이 있어야 아이디 찾기·비밀번호 찾기를 쓸 수 있다.
// 등록·변경에는 현재 비밀번호가 필요하고(토큰을 훔친 사람이 자기 주소로 계정을 가져가지 못하게), 바꾸면 인증 전 상태로 돌아간다.
export default function EmailCard() {
  const { t } = useLocale()
  const queryClient = useQueryClient()
  const me = useQuery({ queryKey: queryKeys.me, queryFn: () => api.me() })
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [localError, setLocalError] = useState<string | null>(null)
  const emailRef = useRef<HTMLInputElement>(null)
  const passwordRef = useRef<HTMLInputElement>(null)

  const save = useMutation({
    mutationFn: () => api.setEmail(email.trim(), password),
    onSuccess: () => {
      setEmail('')
      setPassword('')
      setLocalError(null)
      void queryClient.invalidateQueries({ queryKey: queryKeys.me })
    },
  })
  const resend = useMutation({ mutationFn: () => api.resendVerification() })

  function handleSubmit(e: FormEvent) {
    e.preventDefault()
    if (save.isPending) return
    // 버튼을 끄는 대신 여기서 안내한다(꺼진 버튼은 키보드로 닿지 않아 이유를 알 수 없다)
    if (!looksLikeEmail(email)) {
      setLocalError(t('auth.email_invalid', { fallback: '올바른 이메일 주소를 입력해주세요.' }))
      emailRef.current?.focus()
      return
    }
    if (!password) {
      setLocalError(t('settings.current_pw_required', { fallback: '현재 비밀번호를 입력해주세요.' }))
      passwordRef.current?.focus()
      return
    }
    setLocalError(null)
    resend.reset()
    save.mutate()
  }

  const registered = me.data?.email
  const verified = me.data?.email_verified === true

  return (
    <form className="card settings-card" onSubmit={handleSubmit} aria-labelledby="email-card-title" noValidate>
      <h2 id="email-card-title" className="section-title">
        {t('settings.email_title', { fallback: '이메일' })}
      </h2>
      <p className="muted small">
        {t('settings.email_help', {
          fallback: '인증된 이메일이 있어야 아이디 찾기와 비밀번호 찾기를 쓸 수 있어요.',
        })}
      </p>

      {me.isPending && <LoadingNotice />}
      {me.error && <ErrorNotice error={me.error} onRetry={() => void me.refetch()} />}

      {me.data && (
        <div className="account-status">
          {registered ? (
            <>
              <span className="num">{registered}</span>
              <StatusBadge tone={verified ? 'ok' : 'warn'}>
                {verified
                  ? t('settings.email_verified', { fallback: '인증됨' })
                  : t('settings.email_unverified', { fallback: '인증 전' })}
              </StatusBadge>
            </>
          ) : (
            <span className="muted">{t('settings.email_none', { fallback: '등록한 이메일이 없어요.' })}</span>
          )}
        </div>
      )}

      {registered && !verified && (
        <div className="account-actions">
          <p className="muted small">
            {t('settings.email_verify_hint', { fallback: '메일의 링크를 열면 인증이 끝나요. 메일이 안 왔다면 스팸함을 확인하거나 다시 받아보세요.' })}
          </p>
          <button type="button" className="link-button" onClick={() => !resend.isPending && resend.mutate()} aria-disabled={resend.isPending || undefined}>
            {resend.isPending ? t('common.loading') : t('settings.email_resend', { fallback: '인증 메일 다시 받기' })}
          </button>
          {resend.error && <ErrorNotice error={resend.error} />}
          {resend.data && (
            <p className="notice order-result" role="status">
              {resend.data.message}
            </p>
          )}
        </div>
      )}

      <label className="auth-field">
        <span>{registered ? t('settings.email_new', { fallback: '새 이메일' }) : t('settings.email_label', { fallback: '이메일' })}</span>
        <input
          type="email"
          ref={emailRef}
          aria-invalid={localError && !looksLikeEmail(email) ? true : undefined}
          aria-describedby={localError ? 'email-card-error' : undefined}
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          autoComplete="email"
          inputMode="email"
        />
      </label>
      <label className="auth-field">
        <span>{t('settings.current_password', { fallback: '현재 비밀번호' })}</span>
        <input
          type="password"
          ref={passwordRef}
          aria-invalid={localError && looksLikeEmail(email) && !password ? true : undefined}
          aria-describedby={localError ? 'email-card-error' : undefined}
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          autoComplete="current-password"
        />
        <span className="muted small">
          {t('settings.email_password_help', { fallback: '이메일을 등록하거나 바꿀 때는 본인 확인을 위해 비밀번호가 필요해요.' })}
        </span>
      </label>

      {localError && (
        <p id="email-card-error" className="notice notice-error" role="alert">
          {localError}
        </p>
      )}
      {save.error && <ErrorNotice error={save.error} fallback={t('settings.save_failed')} />}
      {save.data && (
        <p className="notice order-result" role="status">
          {save.data.message}
        </p>
      )}

      <button type="submit" className="send" aria-disabled={save.isPending || undefined}>
        {save.isPending
          ? t('common.loading')
          : registered
            ? t('settings.email_change', { fallback: '이메일 바꾸기' })
            : t('settings.email_register', { fallback: '이메일 등록' })}
      </button>
      <MockMailbox refreshKey={save.data ?? resend.data} />
    </form>
  )
}
