import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useEffect } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api'
import { queryKeys } from '../api/queryKeys'
import { useAuth } from '../auth/useAuth'
import AuthBrand from '../components/AuthBrand'
import ErrorNotice from '../components/ui/ErrorNotice'
import { useHashToken } from '../utils/useHashToken'

// 인증 토큰은 1회용이라 같은 토큰을 두 번 보내면 둘째는 400이다. React StrictMode(개발)가 효과를 두 번 돌려도
// 한 번만 보내도록 이미 보낸 토큰을 기억한다(상태는 같은 컴포넌트가 이어 가므로 결과는 그대로 보인다).
const sent = new Set<string>()

// 메일 링크(/verify-email#token=...)를 열면 화면이 열리자마자 한 번 인증한다. 토큰은 읽자마자 주소창에서 지운다.
export default function VerifyEmailPage() {
  const { user } = useAuth()
  const queryClient = useQueryClient()
  const token = useHashToken()
  const mutation = useMutation({
    mutationFn: (value: string) => api.verifyEmail(value),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: queryKeys.me }),
  })
  const { mutate } = mutation

  useEffect(() => {
    if (!token || sent.has(token)) return
    sent.add(token)
    mutate(token)
  }, [token, mutate])

  return (
    <div className="auth-page">
      <div className="card auth-card">
        <AuthBrand />
        <h1 className="auth-title">이메일 인증</h1>

        {!token && (
          <p className="notice notice-error" role="alert">
            링크가 올바르지 않아요. 메일의 링크를 다시 열어주세요.
          </p>
        )}
        {token && (mutation.isPending || mutation.isIdle) && <p className="muted">인증하고 있어요…</p>}
        {token && mutation.isSuccess && (
          <p className="notice order-result" role="status">
            {mutation.data.message}
          </p>
        )}
        {token && mutation.error && <ErrorNotice error={mutation.error} fallback="이메일을 인증하지 못했어요." />}

        {(mutation.isSuccess || mutation.error || !token) && (
          <p className="muted small auth-switch">
            {user ? (
              <Link to="/settings">설정으로 가기</Link>
            ) : (
              <>
                <Link to="/login">로그인하기</Link>
                {/* 링크가 만료됐으면 로그인한 뒤 설정에서 인증 메일을 다시 받을 수 있다 */}
              </>
            )}
          </p>
        )}
        {(mutation.error || !token) && (
          <p className="muted small">링크가 만료됐다면 로그인한 뒤 설정에서 인증 메일을 다시 받을 수 있어요.</p>
        )}
      </div>
    </div>
  )
}
