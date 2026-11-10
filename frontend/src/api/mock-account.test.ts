import { describe, expect, it } from 'vitest'
import { ApiError } from './errors'
import { mockApi } from './mockApi'
import { issueToken, mockUsers } from './mock'
import { readMockMailbox } from './mock-account'
import { clearToken, getToken, setToken } from '../utils/token'

// 계정 복구·이메일·비밀번호 변경 mock이 backend/API.md(2026-10-30)의 규칙을 따르는지 확인한다.
async function signup(): Promise<{ username: string; password: string }> {
  const username = `acct${Math.random().toString(36).slice(2, 10)}`
  const password = 'Zk9-dummy-pw1'
  setToken((await mockApi.signup(username, password)).access_token)
  return { username, password }
}

const tokenOf = (link: string) => new URLSearchParams(new URL(link).hash.slice(1)).get('token') ?? ''
const lastLink = () => readMockMailbox()[0].link

async function rejected(promise: Promise<unknown>): Promise<ApiError> {
  const err: unknown = await promise.then(
    () => null,
    (e: unknown) => e,
  )
  expect(err).toBeInstanceOf(ApiError)
  return err as ApiError
}

describe('이메일 등록·인증 mock', () => {
  it('현재 비밀번호가 필요하고, 등록하면 인증 전이며, 링크로 인증하면 me에 반영된다', async () => {
    const { password } = await signup()
    const email = `${Math.random().toString(36).slice(2, 8)}@example.com`

    expect((await rejected(mockApi.setEmail(email, 'wrong-dummy-pw-1'))).status).toBe(422)
    expect((await rejected(mockApi.setEmail('not-an-email', password))).status).toBe(422)

    const saved = await mockApi.setEmail(email, password)
    expect(saved).toMatchObject({ email, email_verified: false, verification_sent: true })
    expect(await mockApi.me()).toMatchObject({ email, email_verified: false })

    // 같은 주소로 다시 저장하면 메일을 다시 보내지 않는다
    expect((await mockApi.setEmail(email, password)).verification_sent).toBe(false)

    const token = tokenOf(lastLink())
    expect((await mockApi.verifyEmail(token)).ok).toBe(true)
    expect(await mockApi.me()).toMatchObject({ email_verified: true })

    // 인증 토큰은 1회용이다
    expect((await rejected(mockApi.verifyEmail(token))).status).toBe(400)
    // 이미 인증된 주소는 인증 메일을 다시 받을 수 없다
    expect((await rejected(mockApi.resendVerification())).status).toBe(400)
  })

  it('다른 계정이 쓰는 이메일은 409이고, 다시 보내면 이전 인증 링크는 무효다', async () => {
    const first = await signup()
    const email = `${Math.random().toString(36).slice(2, 8)}@example.com`
    await mockApi.setEmail(email, first.password)
    const firstLink = lastLink()

    await mockApi.resendVerification()
    expect((await rejected(mockApi.verifyEmail(tokenOf(firstLink)))).status).toBe(400)
    expect((await mockApi.verifyEmail(tokenOf(lastLink()))).ok).toBe(true)

    const second = await signup()
    expect((await rejected(mockApi.setEmail(email, second.password))).status).toBe(409)
  })

  it('이메일이 없으면 인증 메일을 다시 받을 수 없다(400)', async () => {
    await signup()
    expect((await rejected(mockApi.resendVerification())).status).toBe(400)
  })
})

describe('아이디·비밀번호 찾기와 재설정 mock', () => {
  async function verifiedAccount() {
    const account = await signup()
    const email = `${Math.random().toString(36).slice(2, 8)}@example.com`
    await mockApi.setEmail(email, account.password)
    await mockApi.verifyEmail(tokenOf(lastLink()))
    return { ...account, email }
  }

  it('이메일이 있든 없든, 인증 전이든 같은 응답이고 메일은 인증된 주소에만 간다', async () => {
    const { email } = await verifiedAccount()
    const before = readMockMailbox().length
    const known = await mockApi.forgotPassword(email)
    const unknown = await mockApi.forgotPassword('nobody-here@example.com')
    expect(known).toEqual(unknown)
    expect(readMockMailbox().length).toBe(before + 1)
    expect((await rejected(mockApi.forgotPassword('bad'))).status).toBe(422)
  })

  it('재설정: 규칙 위반(422)은 링크를 쓰지 않고 남기고, 성공하면 옛 비밀번호·옛 토큰이 무효가 된다', async () => {
    const { username, password, email } = await verifiedAccount()
    const oldToken = getToken()!
    await mockApi.forgotPassword(email)
    const resetToken = tokenOf(lastLink())

    // 아이디가 들어간 비밀번호는 거부되고 링크는 그대로 남는다
    expect((await rejected(mockApi.resetPassword(resetToken, `${username}-pw-1`))).status).toBe(422)

    const done = await mockApi.resetPassword(resetToken, 'New-pass-Phrase-77')
    expect(done.message).toContain('모든 기기에서 로그아웃')
    // 1회용
    expect((await rejected(mockApi.resetPassword(resetToken, 'Another-pass-88'))).status).toBe(400)

    expect((await rejected(mockApi.login(username, password))).status).toBe(401)
    expect((await mockApi.login(username, 'New-pass-Phrase-77')).user.username).toBe(username)
    // 재설정 전에 발급한 토큰은 무효다(실제 서버의 token_version 증가)
    setToken(oldToken)
    expect((await rejected(mockApi.me())).status).toBe(401)
    clearToken()
  })

  it('아이디 찾기도 인증된 이메일에만 메일이 가고 응답은 같다', async () => {
    const { email } = await verifiedAccount()
    const before = readMockMailbox().length
    const known = await mockApi.findUsername(email)
    const unknown = await mockApi.findUsername('nobody-here@example.com')
    expect(known).toEqual(unknown)
    expect(readMockMailbox().length).toBe(before + 1)
  })
})

describe('비밀번호 변경 mock', () => {
  it('현재 비밀번호가 틀리면 422(로그인은 유지), 성공하면 새 토큰이 오고 옛 토큰은 무효다', async () => {
    const { username, password } = await signup()
    const oldToken = getToken()!

    expect((await rejected(mockApi.changePassword('wrong-dummy-pw-1', 'Brand-new-pass-1'))).status).toBe(422)
    expect((await mockApi.me()).username).toBe(username) // 422라서 로그인이 풀리지 않는다
    expect((await rejected(mockApi.changePassword(password, 'short'))).status).toBe(422)

    const res = await mockApi.changePassword(password, 'Brand-new-pass-1')
    expect(res.access_token).not.toBe(oldToken)
    setToken(oldToken)
    expect((await rejected(mockApi.me())).status).toBe(401)
    setToken(res.access_token)
    expect((await mockApi.me()).username).toBe(username)
    expect((await mockApi.login(username, 'Brand-new-pass-1')).user.username).toBe(username)
  })
})

describe('회원 탈퇴 mock (DELETE /auth/me, delete-account/confirm)', () => {
  it('비밀번호 계정: 비밀번호가 필수이고 틀리면 422, 맞으면 지워져 로그인도 옛 토큰도 안 된다', async () => {
    const { username, password } = await signup()
    const token = getToken()!

    expect((await rejected(mockApi.deleteAccount({}))).status).toBe(422)
    expect((await rejected(mockApi.deleteAccount({ current_password: 'wrong-dummy-pw-1' }))).status).toBe(422)
    expect((await mockApi.me()).username).toBe(username) // 422라서 계정은 그대로다

    const done = await mockApi.deleteAccount({ current_password: password })
    expect(done).toMatchObject({ ok: true, status: 'deleted' })

    expect((await rejected(mockApi.login(username, password))).status).toBe(401)
    setToken(token)
    expect((await rejected(mockApi.me())).status).toBe(401) // 지운 뒤 그 사용자의 모든 토큰은 401
    clearToken()
    // 같은 아이디로 다시 가입할 수 있다
    expect((await mockApi.signup(username, password)).user.username).toBe(username)
  })

  it('비밀번호 없는 소셜 계정 + 인증된 이메일: 202로 확인 링크를 보내고, 링크로 확정해야 지워진다(1회용)', async () => {
    const username = `soc${Math.random().toString(36).slice(2, 8)}`
    const user = { id: 9000 + Math.floor(Math.random() * 900), username, password: '', email: `${username}@example.com`, emailVerified: true }
    mockUsers.push(user)
    setToken(issueToken(user.id))

    const sent = await mockApi.deleteAccount({})
    expect(sent.status).toBe('confirmation_sent')
    expect((await mockApi.me()).username).toBe(username) // 아직 지워지지 않았다
    const token = tokenOf(lastLink())
    expect(lastLink()).toContain('/delete-account#token=')

    // 다른 목적의 토큰(재설정·인증)으로는 확정할 수 없고, 같은 링크는 한 번만 쓸 수 있다
    expect((await rejected(mockApi.confirmDeleteAccount('not-a-real-token'))).status).toBe(400)
    expect((await mockApi.confirmDeleteAccount(token)).status).toBe('deleted')
    expect((await rejected(mockApi.confirmDeleteAccount(token))).status).toBe(400)
    expect((await rejected(mockApi.me())).status).toBe(401)
    clearToken()
  })

  it('비밀번호도 인증된 이메일도 없는 소셜 계정: 방금 로그인했고 아이디가 맞아야만 지운다(아니면 400)', async () => {
    const username = `soc${Math.random().toString(36).slice(2, 8)}`
    const user = { id: 9000 + Math.floor(Math.random() * 900), username, password: '' }
    mockUsers.push(user)
    setToken(issueToken(user.id))

    expect((await rejected(mockApi.deleteAccount({}))).status).toBe(400)
    expect((await rejected(mockApi.deleteAccount({ confirm_username: 'someone-else' }))).status).toBe(400)
    expect((await mockApi.deleteAccount({ confirm_username: username.toUpperCase() })).status).toBe('deleted')
    clearToken()
  })
})
