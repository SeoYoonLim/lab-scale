import { useMutation } from '@tanstack/react-query'
import { useState } from 'react'
import type { FormEvent } from 'react'
import { api } from '../../api'
import { PASSWORD_MAX_LEN, PASSWORD_MIN_LEN } from '../../api/contracts/account'
import ErrorNotice from '../../components/ui/ErrorNotice'
import { useLocale } from '../../i18n/useLocale'
import { setToken } from '../../utils/token'

// 비밀번호 변경(POST /api/auth/change-password). 성공하면 서버가 이 계정의 기존 토큰을 전부 무효로 하므로(이 기기 것 포함)
// 응답으로 온 새 토큰으로 바꿔 저장해야 다음 요청이 401이 되지 않는다. 현재 비밀번호가 틀린 것은 401이 아니라 422라서
// 로그인 상태가 유지되고, 연속으로 틀리면 429로 잠깐 잠긴다.
export default function PasswordCard() {
  const { t } = useLocale()
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [confirm, setConfirm] = useState('')
  const [localError, setLocalError] = useState<string | null>(null)

  const mutation = useMutation({
    mutationFn: () => api.changePassword(current, next),
    onSuccess: (res) => {
      setToken(res.access_token)
      setCurrent('')
      setNext('')
      setConfirm('')
    },
  })

  function handleSubmit(e: FormEvent) {
    e.preventDefault()
    if (mutation.isPending) return
    if (next.length < PASSWORD_MIN_LEN || next.length > PASSWORD_MAX_LEN) {
      setLocalError(
        t('settings.password_length', {
          fallback: '새 비밀번호는 {min}~{max}자여야 해요.',
          vars: { min: PASSWORD_MIN_LEN, max: PASSWORD_MAX_LEN },
        }),
      )
      return
    }
    if (next !== confirm) {
      setLocalError(t('settings.password_mismatch', { fallback: '새 비밀번호 확인이 일치하지 않아요.' }))
      return
    }
    setLocalError(null)
    mutation.mutate()
  }

  return (
    <form className="card settings-card" onSubmit={handleSubmit} aria-labelledby="password-card-title">
      <h2 id="password-card-title" className="section-title">
        {t('settings.password_title', { fallback: '비밀번호 변경' })}
      </h2>
      <label className="auth-field">
        <span>{t('settings.current_password', { fallback: '현재 비밀번호' })}</span>
        <input
          type="password"
          value={current}
          onChange={(e) => setCurrent(e.target.value)}
          autoComplete="current-password"
        />
      </label>
      <label className="auth-field">
        <span>{t('settings.new_password', { fallback: '새 비밀번호' })}</span>
        <input type="password" value={next} onChange={(e) => setNext(e.target.value)} autoComplete="new-password" />
        <span className="muted small">
          {t('settings.password_help', {
            fallback: '{min}~{max}자. 흔한 비밀번호나 아이디·이메일이 들어간 비밀번호는 쓸 수 없어요.',
            vars: { min: PASSWORD_MIN_LEN, max: PASSWORD_MAX_LEN },
          })}
        </span>
      </label>
      <label className="auth-field">
        <span>{t('settings.new_password_confirm', { fallback: '새 비밀번호 확인' })}</span>
        <input
          type="password"
          value={confirm}
          onChange={(e) => setConfirm(e.target.value)}
          autoComplete="new-password"
        />
      </label>

      {localError && (
        <p className="notice notice-error" role="alert">
          {localError}
        </p>
      )}
      {mutation.error && <ErrorNotice error={mutation.error} fallback={t('settings.save_failed')} />}
      {mutation.isSuccess && (
        <p className="notice order-result" role="status">
          {t('settings.password_changed', {
            fallback: '비밀번호를 바꿨어요. 이 기기는 로그인된 채로 유지되고, 다른 기기에서는 모두 로그아웃돼요.',
          })}
        </p>
      )}

      <button type="submit" className="send" disabled={mutation.isPending || !current || !next || !confirm}>
        {mutation.isPending ? t('common.loading') : t('settings.password_change', { fallback: '비밀번호 바꾸기' })}
      </button>
    </form>
  )
}
