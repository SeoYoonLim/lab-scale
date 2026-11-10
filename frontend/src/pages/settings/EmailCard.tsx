import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import type { FormEvent } from 'react'
import { api } from '../../api'
import { looksLikeEmail } from '../../api/contracts/account'
import { queryKeys } from '../../api/queryKeys'
import MockMailbox from '../../components/MockMailbox'
import ErrorNotice from '../../components/ui/ErrorNotice'
import StatusBadge from '../../components/ui/StatusBadge'
import { useLocale } from '../../i18n/useLocale'

// 복구용 이메일(PUT /api/auth/email). 인증된 이메일이 있어야 아이디 찾기·비밀번호 찾기를 쓸 수 있다.
// 등록·변경에는 현재 비밀번호가 필요하고(토큰을 훔친 사람이 자기 주소로 계정을 가져가지 못하게), 바꾸면 인증 전 상태로 돌아간다.
export default function EmailCard() {
  const { t } = useLocale()
  const queryClient = useQueryClient()
  const me = useQuery({ queryKey: queryKeys.me, queryFn: () => api.me() })
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')

  const save = useMutation({
    mutationFn: () => api.setEmail(email.trim(), password),
    onSuccess: () => {
      setEmail('')
      setPassword('')
      void queryClient.invalidateQueries({ queryKey: queryKeys.me })
    },
  })
  const resend = useMutation({ mutationFn: () => api.resendVerification() })

  function handleSubmit(e: FormEvent) {
    e.preventDefault()
    if (save.isPending || !looksLikeEmail(email) || !password) return
    resend.reset()
    save.mutate()
  }

  const registered = me.data?.email
  const verified = me.data?.email_verified === true

  return (
    <form className="card settings-card" onSubmit={handleSubmit} aria-labelledby="email-card-title">
      <h2 id="email-card-title" className="section-title">
        {t('settings.email_title', { fallback: '이메일' })}
      </h2>
      <p className="muted small">
        {t('settings.email_help', {
          fallback: '인증된 이메일이 있어야 아이디 찾기와 비밀번호 찾기를 쓸 수 있어요.',
        })}
      </p>

      {me.isPending && <p className="muted">{t('common.loading')}</p>}
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
          <button type="button" className="link-button" onClick={() => resend.mutate()} disabled={resend.isPending}>
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
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          autoComplete="current-password"
        />
        <span className="muted small">
          {t('settings.email_password_help', { fallback: '이메일을 등록하거나 바꿀 때는 본인 확인을 위해 비밀번호가 필요해요.' })}
        </span>
      </label>

      {save.error && <ErrorNotice error={save.error} fallback={t('settings.save_failed')} />}
      {save.data && (
        <p className="notice order-result" role="status">
          {save.data.message}
        </p>
      )}

      <button type="submit" className="send" disabled={save.isPending || !looksLikeEmail(email) || !password}>
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
