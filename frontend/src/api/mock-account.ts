import type { AuthResponse } from '../types'
import { looksLikeEmail } from './contracts/account'
import type { AccountDeleteResponse, DeleteAccountInput, EmailStatus, MessageResponse } from './contracts/account'
import type { RecoveryApi } from './domains/recovery'
import { ApiError } from './errors'
import { currentUserId, issueToken, mockUsers, saveUsers, toUserOut, validatePassword } from './mock'
import type { MockUser } from './mock'
import { delay, unauthorized } from './mock-core'
import { getToken } from '../utils/token'

// 이메일 등록·인증, 아이디/비밀번호 찾기·재설정, 비밀번호 변경 mock. 메시지·상태코드는 backend/API.md(2026-10-30)를 따른다.
// 실제 서버는 메일로 링크를 보내고 개발 모드에서는 dev_mail.log에 적는다. mock에는 그 자리에 "샘플 메일함"(localStorage)이 있어서
// 화면이 링크를 보여준다(MockMailbox). 링크의 토큰은 새 페이지로 열어도 살아 있어야 해서 메모리가 아니라 localStorage에 둔다.
type Purpose = 'verify_email' | 'reset_password' | 'delete_account'

interface MockToken {
  token: string
  user_id: number
  purpose: Purpose
  expires_at: number
  used: boolean
}

export interface MockMail {
  id: number
  to: string
  subject: string
  link: string
  sent_at: string
}

interface MailState {
  tokens: MockToken[]
  mailbox: MockMail[]
  nextMailId: number
}

const STORE_KEY = 'mock_account_mail'
const TOKEN_TTL_MS: Record<Purpose, number> = {
  verify_email: 24 * 3_600_000,
  reset_password: 30 * 60_000,
  delete_account: 30 * 60_000,
}
const LINK_ERROR = '링크가 올바르지 않거나 만료됐습니다. 메일을 다시 요청해주세요.'
const NEUTRAL_MESSAGE =
  '입력한 이메일이 인증된 계정의 이메일이면 안내 메일을 보냈습니다. 몇 분 안에 받지 못했다면 스팸함을 확인하거나, 로그인한 뒤 이메일 등록·인증 상태를 확인해주세요.'
const WRONG_CURRENT_PASSWORD = '현재 비밀번호가 올바르지 않습니다.'
const DELETED_MESSAGE = '회원 탈퇴가 끝났습니다. 계정과 모든 데이터를 지웠습니다.'
const DELETE_NEEDS_RECENT_LOGIN =
  '이 계정은 비밀번호와 인증된 이메일이 없어 본인 확인을 위해 방금(10분 안) 다시 로그인한 뒤, 확인용으로 아이디를 함께 보내야 탈퇴할 수 있습니다.'

function load(): MailState {
  try {
    const raw = localStorage.getItem(STORE_KEY)
    if (raw) {
      const parsed = JSON.parse(raw) as MailState
      if (Array.isArray(parsed.tokens) && Array.isArray(parsed.mailbox)) return parsed
    }
  } catch {
    // localStorage를 못 쓰면 이번 세션 메모리만으로 동작한다
  }
  return { tokens: [], mailbox: [], nextMailId: 1 }
}

let state: MailState = load()

function save(): void {
  try {
    localStorage.setItem(STORE_KEY, JSON.stringify(state))
  } catch {
    // no-op
  }
}

// 다른 탭에서 보낸 메일도 보이게 읽을 때마다 다시 불러온다
function fresh(): MailState {
  state = load()
  return state
}

export function readMockMailbox(): MockMail[] {
  return [...fresh().mailbox].reverse()
}

if (import.meta.env.DEV && typeof window !== 'undefined') {
  ;(window as unknown as Record<string, unknown>).__mockMailbox = {
    all: readMockMailbox,
    last: () => readMockMailbox()[0] ?? null,
  }
}

function newToken(): string {
  return `${Math.random().toString(36).slice(2)}${Math.random().toString(36).slice(2)}${Date.now().toString(36)}`
}

function voidTokens(userId: number, purpose: Purpose): void {
  for (const t of state.tokens) if (t.user_id === userId && t.purpose === purpose) t.used = true
}

function linkPath(purpose: Purpose): string {
  if (purpose === 'verify_email') return '/verify-email'
  if (purpose === 'reset_password') return '/reset-password'
  return '/delete-account'
}

function sendMail(user: MockUser, purpose: Purpose): void {
  fresh()
  voidTokens(user.id, purpose) // 다시 보내면 이전 링크는 무효
  const token = newToken()
  state.tokens.push({ token, user_id: user.id, purpose, expires_at: Date.now() + TOKEN_TTL_MS[purpose], used: false })
  const path = linkPath(purpose)
  const subject = {
    verify_email: '[AI 투자 리서치] 이메일 인증',
    reset_password: '[AI 투자 리서치] 비밀번호 재설정',
    delete_account: '[AI 투자 리서치] 회원 탈퇴 확인',
  }[purpose]
  state.mailbox.push({
    id: state.nextMailId++,
    to: user.email ?? '',
    subject,
    link: `${window.location.origin}${path}#token=${token}`,
    sent_at: new Date().toISOString(),
  })
  save()
}

function sendUsernameMail(user: MockUser): void {
  fresh()
  state.mailbox.push({
    id: state.nextMailId++,
    to: user.email ?? '',
    subject: '[AI 투자 리서치] 아이디 안내',
    link: `${window.location.origin}/login`,
    sent_at: new Date().toISOString(),
  })
  save()
}

function takeToken(token: string, purpose: Purpose): MockToken {
  const found = fresh().tokens.find((t) => t.token === token && t.purpose === purpose)
  if (!found || found.used || found.expires_at < Date.now()) throw new ApiError(LINK_ERROR, 400)
  return found
}

function currentUser(): MockUser {
  const user = mockUsers.find((u) => u.id === currentUserId())
  if (!user) throw unauthorized()
  return user
}

function requireEmailFormat(email: string): string {
  const normalized = email.trim().toLowerCase()
  if (!looksLikeEmail(normalized)) throw new ApiError('이메일 형식이 올바르지 않습니다.', 422)
  return normalized
}

// 비밀번호에 아이디나 이메일 앞부분(3자 이상)이 들어 있으면 거부한다(가입과 같은 규칙 + 이메일)
function validateNewPassword(user: MockUser, password: string): void {
  validatePassword(password, user.username)
  const local = user.email?.split('@')[0]?.toLowerCase()
  if (local && local.length >= 3 && password.toLowerCase().includes(local)) {
    throw new ApiError('이메일 아이디가 들어간 비밀번호는 사용할 수 없습니다.', 422)
  }
}

async function setEmail(email: string, currentPassword: string): Promise<EmailStatus> {
  await delay(250)
  const user = currentUser()
  if (!currentPassword) throw new ApiError('현재 비밀번호를 입력해주세요.', 422)
  if (user.password !== currentPassword) throw new ApiError(WRONG_CURRENT_PASSWORD, 422)
  const normalized = requireEmailFormat(email)
  if (mockUsers.some((u) => u.id !== user.id && u.email === normalized)) throw new ApiError('이미 사용 중인 이메일입니다.', 409)
  if (user.email === normalized) {
    return { email: normalized, email_verified: user.emailVerified ?? false, verification_sent: false, message: '이미 등록된 이메일입니다.' }
  }
  user.email = normalized
  user.emailVerified = false
  fresh()
  voidTokens(user.id, 'reset_password') // 옛 주소로 간 재설정 링크가 살아 있지 않게
  saveUsers()
  sendMail(user, 'verify_email')
  return {
    email: normalized,
    email_verified: false,
    verification_sent: true,
    message: '인증 메일을 보냈습니다. 메일의 링크를 열면 인증이 끝납니다.',
  }
}

async function resendVerification(): Promise<MessageResponse> {
  await delay(200)
  const user = currentUser()
  if (!user.email) throw new ApiError('등록된 이메일이 없습니다. 먼저 이메일을 등록해주세요.', 400)
  if (user.emailVerified) throw new ApiError('이미 인증된 이메일입니다.', 400)
  sendMail(user, 'verify_email')
  return { ok: true, message: '인증 메일을 다시 보냈습니다. 메일의 링크를 열면 인증이 끝납니다.' }
}

async function verifyEmail(token: string): Promise<MessageResponse> {
  await delay(200)
  const found = takeToken(token, 'verify_email')
  found.used = true
  const user = mockUsers.find((u) => u.id === found.user_id)
  if (user) user.emailVerified = true
  save()
  saveUsers()
  return { ok: true, message: '이메일 인증이 끝났습니다.' }
}

// 이메일이 있든 없든, 인증 전이든 같은 응답이다(계정 존재 여부를 드러내지 않는다)
async function findUsername(email: string): Promise<MessageResponse> {
  await delay(250)
  const normalized = requireEmailFormat(email)
  const user = mockUsers.find((u) => u.email === normalized && u.emailVerified)
  if (user) sendUsernameMail(user)
  return { ok: true, message: NEUTRAL_MESSAGE }
}

async function forgotPassword(email: string): Promise<MessageResponse> {
  await delay(250)
  const normalized = requireEmailFormat(email)
  const user = mockUsers.find((u) => u.email === normalized && u.emailVerified)
  if (user) sendMail(user, 'reset_password')
  return { ok: true, message: NEUTRAL_MESSAGE }
}

async function resetPassword(token: string, newPassword: string): Promise<MessageResponse> {
  await delay(250)
  const found = takeToken(token, 'reset_password')
  const user = mockUsers.find((u) => u.id === found.user_id)
  if (!user) throw new ApiError(LINK_ERROR, 400)
  // 규칙 위반은 422이고 토큰은 쓰이지 않고 남는다
  validateNewPassword(user, newPassword)
  found.used = true
  user.password = newPassword
  user.tokensValidFrom = Date.now()
  voidTokens(user.id, 'reset_password')
  save()
  saveUsers()
  return { ok: true, message: '비밀번호를 바꿨습니다. 모든 기기에서 로그아웃됐으니 새 비밀번호로 로그인해주세요.' }
}

async function changePassword(currentPassword: string, newPassword: string): Promise<AuthResponse> {
  await delay(250)
  const user = currentUser()
  if (user.password !== currentPassword) throw new ApiError(WRONG_CURRENT_PASSWORD, 422)
  validateNewPassword(user, newPassword)
  user.password = newPassword
  user.tokensValidFrom = Date.now()
  saveUsers()
  // 기존 토큰은 전부 무효가 되고, 응답의 새 토큰으로 현재 세션을 이어 간다
  return { user: toUserOut(user), access_token: issueToken(user.id), token_type: 'bearer' }
}

// 계정과 그 계정의 토큰을 지운다(실제 서버는 관심종목·모의투자·리서치 기록·진단까지 지운다. mock의 사용자별 저장소는 메모리라 계정이
// 사라지면 더는 접근할 수 없다). 지운 뒤 그 사용자의 모든 토큰은 401이다(currentUserId가 계정이 없으면 401로 본다).
function removeAccount(user: MockUser): AccountDeleteResponse {
  fresh()
  state.tokens = state.tokens.filter((t) => t.user_id !== user.id)
  save()
  mockUsers.splice(mockUsers.indexOf(user), 1)
  saveUsers()
  return { ok: true, status: 'deleted', message: DELETED_MESSAGE }
}

// 본인 확인은 계정 종류에 따라 다르다(backend/API.md). 비밀번호가 있는 계정은 current_password가 필수다. mock 계정은 모두
// 비밀번호가 있지만, 비밀번호가 빈 계정(소셜 계정 흉내, 테스트에서만 만든다)은 서버와 같은 202/400 경로를 탄다.
async function deleteAccount(input: DeleteAccountInput): Promise<AccountDeleteResponse> {
  await delay(300)
  const user = currentUser()
  if (user.password) {
    if (input.current_password === undefined) throw new ApiError('Field required', 422)
    if (user.password !== input.current_password) throw new ApiError(WRONG_CURRENT_PASSWORD, 422)
    return removeAccount(user)
  }
  if (user.email && user.emailVerified) {
    sendMail(user, 'delete_account')
    return {
      ok: true,
      status: 'confirmation_sent',
      message: '등록된 이메일로 탈퇴 확인 링크를 보냈습니다. 30분 안에 링크에서 확인하면 계정과 모든 데이터가 지워집니다.',
    }
  }
  const issuedAt = Number(getToken()?.match(/^mock\.\d+\.(\d+)\./)?.[1])
  const recent = Number.isFinite(issuedAt) && Date.now() - issuedAt <= 10 * 60_000
  if (!recent || (input.confirm_username ?? '').trim().toLowerCase() !== user.username) {
    throw new ApiError(DELETE_NEEDS_RECENT_LOGIN, 400)
  }
  return removeAccount(user)
}

async function confirmDeleteAccount(token: string): Promise<AccountDeleteResponse> {
  await delay(250)
  const found = takeToken(token, 'delete_account')
  const user = mockUsers.find((u) => u.id === found.user_id)
  if (!user) throw new ApiError(LINK_ERROR, 400)
  found.used = true
  return removeAccount(user)
}

export const accountMock: RecoveryApi = {
  setEmail,
  resendVerification,
  verifyEmail,
  findUsername,
  forgotPassword,
  resetPassword,
  changePassword,
  deleteAccount,
  confirmDeleteAccount,
}
