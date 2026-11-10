import { useMutation } from '@tanstack/react-query'
import { useState } from 'react'
import type { FormEvent } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api'
import { PASSWORD_MAX_LEN, PASSWORD_MIN_LEN } from '../api/contracts/account'
import { ApiError } from '../api/errors'
import { useAuth } from '../auth/useAuth'
import AuthBrand from '../components/AuthBrand'
import ErrorNotice from '../components/ui/ErrorNotice'
import { useHashToken } from '../utils/useHashToken'

// 메일 링크(/reset-password#token=...)로 들어와 새 비밀번호를 정한다. 토큰은 읽자마자 주소창에서 지운다.
// 400(링크가 틀리거나 만료·이미 사용)이면 새 링크를 받게 하고, 422(비밀번호 규칙 위반)면 토큰이 남아 있으니 다시 입력하게 한다.
// 성공하면 서버가 그 계정의 모든 기기를 로그아웃시킨다(토큰을 주지 않는다) - 이 브라우저도 로그인돼 있었다면 같이 정리한다.
export default function ResetPasswordPage() {
  const token = useHashToken()
  // 같은 탭에서 다른 링크로 바뀌면 폼을 새로 만들어 이전 링크의 결과(성공·오류)와 입력을 비운다
  return <ResetPasswordForm key={token ?? ''} token={token} />
}

function ResetPasswordForm({ token }: { token: string | null }) {
  const { user, logout } = useAuth()
  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [localError, setLocalError] = useState<string | null>(null)

  const mutation = useMutation({
    mutationFn: (newPassword: string) => api.resetPassword(token ?? '', newPassword),
    onSuccess: () => {
      if (user) void logout()
    },
  })

  function handleSubmit(e: FormEvent) {
    e.preventDefault()
    if (mutation.isPending || !token) return
    if (password.length < PASSWORD_MIN_LEN || password.length > PASSWORD_MAX_LEN) {
      setLocalError(`비밀번호는 ${PASSWORD_MIN_LEN}~${PASSWORD_MAX_LEN}자여야 해요.`)
      return
    }
    if (password !== confirm) {
      setLocalError('비밀번호 확인이 일치하지 않아요.')
      return
    }
    setLocalError(null)
    mutation.mutate(password)
  }

  // 400은 링크 자체가 못 쓰게 된 것이라 다시 입력해도 소용없다
  const linkDead = mutation.error instanceof ApiError && mutation.error.status === 400

  return (
    <div className="auth-page">
      <form className="card auth-card" onSubmit={handleSubmit}>
        <AuthBrand />
        <h1 className="auth-title">비밀번호 재설정</h1>

        {!token && (
          <>
            <p className="notice notice-error" role="alert">
              링크가 올바르지 않아요. 메일의 링크를 다시 열거나, 새 링크를 받아주세요.
            </p>
            <Link to="/forgot-password" className="link-button">
              비밀번호 찾기
            </Link>
          </>
        )}

        {token && mutation.isSuccess && (
          <>
            <p className="notice order-result" role="status">
              {mutation.data.message}
            </p>
            <Link to="/login" className="send auth-submit auth-link-button">
              로그인하기
            </Link>
          </>
        )}

        {token && !mutation.isSuccess && (
          <>
            <p className="muted small">
              새 비밀번호를 정해주세요. {PASSWORD_MIN_LEN}~{PASSWORD_MAX_LEN}자이고, 흔한 비밀번호나 아이디·이메일이 들어간 비밀번호는 쓸 수 없어요.
            </p>
            <label className="auth-field">
              <span>새 비밀번호</span>
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="new-password"
              />
            </label>
            <label className="auth-field">
              <span>새 비밀번호 확인</span>
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
            {mutation.error && <ErrorNotice error={mutation.error} fallback="비밀번호를 바꾸지 못했어요." />}
            {linkDead ? (
              <Link to="/forgot-password" className="send auth-submit auth-link-button">
                새 링크 받기
              </Link>
            ) : (
              <button type="submit" className="send auth-submit" disabled={mutation.isPending || !password || !confirm}>
                {mutation.isPending ? '바꾸는 중…' : '비밀번호 바꾸기'}
              </button>
            )}
          </>
        )}
      </form>
    </div>
  )
}
