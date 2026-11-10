import { useMutation } from '@tanstack/react-query'
import { useState } from 'react'
import type { FormEvent } from 'react'
import { api } from '../../api'
import { isAccountDeleted } from '../../api/contracts/account'
import { useAuth } from '../../auth/useAuth'
import ErrorNotice from '../../components/ui/ErrorNotice'
import { useLocale } from '../../i18n/useLocale'

// 회원 탈퇴(DELETE /api/auth/me). 계정과 모든 데이터(관심종목, 모의투자 계좌·보유·체결, 리서치 기록, 포트폴리오 진단)를 지우고
// 되돌릴 수 없어서, 먼저 펼쳐서 무엇이 지워지는지 보여주고 비밀번호와 "이해했어요" 체크를 받은 뒤에만 보낸다.
// 비밀번호 계정은 200 deleted이고, 소셜 계정이 인증된 이메일로 확인 링크를 받는 경우(202 confirmation_sent)에는 계정이 아직 있으니
// 로그아웃하지 않고 서버 문장만 보여준다.
export default function DeleteAccountCard() {
  const { t } = useLocale()
  const { endSession } = useAuth()
  const [open, setOpen] = useState(false)
  const [password, setPassword] = useState('')
  const [understood, setUnderstood] = useState(false)

  const mutation = useMutation({
    mutationFn: () => api.deleteAccount({ current_password: password }),
    onSuccess: (res) => {
      if (!isAccountDeleted(res)) return
      // 계정이 없으니 서버 로그아웃을 부르지 않고 이 기기의 로그인 상태만 비운다. RequireAuth가 로그인 화면으로 보내고,
      // 로그인 화면이 서버 문장(탈퇴 완료)을 한 번 보여준다.
      endSession(res.message)
    },
  })

  function close() {
    setOpen(false)
    setPassword('')
    setUnderstood(false)
    mutation.reset()
  }

  function handleSubmit(e: FormEvent) {
    e.preventDefault()
    if (mutation.isPending || !password || !understood) return
    mutation.mutate()
  }

  return (
    <form className="card settings-card danger-zone" onSubmit={handleSubmit} aria-labelledby="delete-card-title">
      <h2 id="delete-card-title" className="section-title">
        {t('settings.delete_title', { fallback: '회원 탈퇴' })}
      </h2>
      <p className="muted small">
        {t('settings.delete_help', {
          fallback: '계정과 모든 데이터(관심종목, 모의투자 계좌·보유·체결, 리서치 기록, 포트폴리오 진단)가 지워지고 되돌릴 수 없어요.',
        })}
      </p>

      {!open ? (
        <button type="button" className="send danger-button" onClick={() => setOpen(true)}>
          {t('settings.delete_open', { fallback: '회원 탈퇴 진행' })}
        </button>
      ) : (
        <>
          <label className="auth-field">
            <span>{t('settings.current_password', { fallback: '현재 비밀번호' })}</span>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="current-password"
            />
          </label>
          <label className="check-label">
            <input type="checkbox" checked={understood} onChange={(e) => setUnderstood(e.target.checked)} />
            <span>{t('settings.delete_understood', { fallback: '모든 데이터가 지워지고 되돌릴 수 없다는 것을 이해했어요.' })}</span>
          </label>

          {mutation.error && <ErrorNotice error={mutation.error} fallback={t('settings.save_failed')} />}
          {/* 소셜 계정은 지우기 전에 이메일 확인이 필요하다(202) - 계정은 아직 그대로다 */}
          {mutation.data && !isAccountDeleted(mutation.data) && (
            <p className="notice order-result" role="status">
              {mutation.data.message}
            </p>
          )}

          <div className="form-actions">
            <button type="submit" className="send danger-button" disabled={mutation.isPending || !password || !understood}>
              {mutation.isPending ? t('common.loading') : t('settings.delete_submit', { fallback: '영구히 탈퇴하기' })}
            </button>
            <button type="button" className="link-button" onClick={close} disabled={mutation.isPending}>
              {t('common.cancel', { fallback: '취소' })}
            </button>
          </div>
        </>
      )}
    </form>
  )
}
