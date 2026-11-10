import { useMutation } from '@tanstack/react-query'
import { useState } from 'react'
import type { FormEvent } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api'
import { looksLikeEmail } from '../api/contracts/account'
import AuthBrand from '../components/AuthBrand'
import MockMailbox from '../components/MockMailbox'
import ErrorNotice from '../components/ui/ErrorNotice'

// 아이디 찾기·비밀번호 찾기 요청(공개). 두 API 모두 이메일이 있든 없든, 인증 전이든 같은 200과 같은 문장을 돌려준다
// (계정 존재 여부를 숨기려고). 그래서 화면도 "메일을 보냈다"고 단정하지 않고 서버가 준 문장을 그대로 보여준다.
const COPY = {
  password: {
    title: '비밀번호 찾기',
    lead: '가입할 때 등록하고 인증한 이메일을 입력하면 비밀번호를 다시 정할 수 있는 링크를 보내드려요. 링크는 30분 동안만, 한 번만 쓸 수 있어요.',
    submit: '재설정 링크 받기',
  },
  username: {
    title: '아이디 찾기',
    lead: '가입할 때 등록하고 인증한 이메일을 입력하면 아이디를 메일로 알려드려요.',
    submit: '아이디 받기',
  },
} as const

export default function RecoveryRequestPage({ mode }: { mode: 'password' | 'username' }) {
  const copy = COPY[mode]
  const [email, setEmail] = useState('')
  const mutation = useMutation({
    mutationFn: (value: string) => (mode === 'password' ? api.forgotPassword(value) : api.findUsername(value)),
  })

  function handleSubmit(e: FormEvent) {
    e.preventDefault()
    if (mutation.isPending || !looksLikeEmail(email)) return
    mutation.mutate(email.trim())
  }

  return (
    <div className="auth-page">
      <form className="card auth-card" onSubmit={handleSubmit}>
        <AuthBrand />
        <h1 className="auth-title">{copy.title}</h1>
        <p className="muted small">{copy.lead}</p>
        <label className="auth-field">
          <span>이메일</span>
          <input
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            autoComplete="email"
            inputMode="email"
          />
        </label>
        {mutation.error && <ErrorNotice error={mutation.error} fallback="요청에 실패했어요. 잠시 후 다시 시도해주세요." />}
        {mutation.data && (
          <p className="notice order-result" role="status">
            {mutation.data.message}
          </p>
        )}
        <button type="submit" className="send auth-submit" disabled={mutation.isPending || !looksLikeEmail(email)}>
          {mutation.isPending ? '보내는 중…' : copy.submit}
        </button>
        <MockMailbox refreshKey={mutation.data} />
        <p className="muted small auth-switch">
          <Link to="/login">로그인으로 돌아가기</Link>
          {' · '}
          {mode === 'password' ? <Link to="/find-username">아이디 찾기</Link> : <Link to="/forgot-password">비밀번호 찾기</Link>}
        </p>
      </form>
    </div>
  )
}
