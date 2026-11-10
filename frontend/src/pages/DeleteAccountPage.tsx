import { useMutation } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { api } from '../api'
import { isAccountDeleted } from '../api/contracts/account'
import { useAuth } from '../auth/useAuth'
import AuthBrand from '../components/AuthBrand'
import ErrorNotice from '../components/ui/ErrorNotice'
import { useHashToken } from '../utils/useHashToken'

// 탈퇴 확인 메일 링크(/delete-account#token=...)로 들어와 탈퇴를 확정한다(소셜 계정이 인증된 이메일로 받는 링크, 30분·1회용).
// 지우는 것은 되돌릴 수 없어서, 인증 링크와 달리 열자마자 보내지 않고 이 화면의 버튼을 눌러야 보낸다(메일 보안 검사기가
// 링크를 미리 열어 보는 일이 있어도 계정이 지워지지 않게). 토큰은 읽자마자 주소창에서 지운다.
export default function DeleteAccountPage() {
  const token = useHashToken()
  // 같은 탭에서 다른 링크로 바뀌면 폼을 새로 만들어 이전 결과를 비운다
  return <DeleteAccountConfirm key={token ?? ''} token={token} />
}

function DeleteAccountConfirm({ token }: { token: string | null }) {
  const { user, endSession } = useAuth()
  const mutation = useMutation({
    mutationFn: () => api.confirmDeleteAccount(token ?? ''),
    onSuccess: (res) => {
      // 이 브라우저가 지워진 계정으로 로그인돼 있었다면 같이 정리한다
      if (isAccountDeleted(res) && user) endSession()
    },
  })
  // 400(링크가 틀리거나 만료·이미 사용)이면 다시 눌러도 소용없다
  const done = mutation.isSuccess

  return (
    <div className="auth-page">
      <div className="card auth-card">
        <AuthBrand />
        <h1 className="auth-title">회원 탈퇴 확인</h1>

        {!token && (
          <p className="notice notice-error" role="alert">
            링크가 올바르지 않아요. 메일의 링크를 다시 열어주세요.
          </p>
        )}

        {token && !done && (
          <>
            <p className="muted small">
              계정과 모든 데이터(관심종목, 모의투자 계좌·보유·체결, 리서치 기록, 포트폴리오 진단)가 지워지고 되돌릴 수 없어요. 정말 탈퇴하려면 아래
              버튼을 눌러주세요.
            </p>
            {mutation.error && <ErrorNotice error={mutation.error} fallback="탈퇴를 확정하지 못했어요." />}
            <button
              type="button"
              className="send auth-submit danger-button"
              onClick={() => mutation.mutate()}
              disabled={mutation.isPending}
            >
              {mutation.isPending ? '처리 중…' : '영구히 탈퇴하기'}
            </button>
          </>
        )}

        {done && (
          <p className="notice order-result" role="status">
            {mutation.data.message}
          </p>
        )}

        <p className="muted small auth-switch">
          <Link to={user && !done ? '/settings' : '/login'}>{user && !done ? '설정으로 돌아가기' : '로그인 화면으로'}</Link>
        </p>
      </div>
    </div>
  )
}
