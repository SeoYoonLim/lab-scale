import { z } from 'zod'

// 계정 복구·이메일·비밀번호 변경(backend/API.md 2026-10-29~30). 로그인 응답(AuthResponse)은 기존 스키마를 그대로 쓴다.
export interface MessageResponse {
  ok: boolean
  message: string
}

// PUT /api/auth/email 응답. 지금과 같은 주소로 다시 저장하면 verification_sent가 false다.
export interface EmailStatus {
  email: string | null
  email_verified: boolean
  verification_sent: boolean
  message: string
}

export const messageResponseSchema = z.object({
  ok: z.boolean(),
  message: z.string(),
}) satisfies z.ZodType<MessageResponse>

export const emailStatusSchema = z.object({
  email: z.string().nullable(),
  email_verified: z.boolean(),
  verification_sent: z.boolean(),
  message: z.string(),
}) satisfies z.ZodType<EmailStatus>

// DELETE /api/auth/me, POST /api/auth/delete-account/confirm 응답. status는 'deleted'(지웠다) 또는 'confirmation_sent'
// (소셜 계정: 인증된 이메일로 30분짜리 확인 링크를 보냈다 - 링크를 열어 확정해야 지워진다).
export interface AccountDeleteResponse {
  ok: boolean
  status: string
  message: string
}

export const accountDeleteResponseSchema = z.object({
  ok: z.boolean(),
  status: z.string(),
  message: z.string(),
}) satisfies z.ZodType<AccountDeleteResponse>

// 비밀번호가 있는 계정은 current_password만 보낸다. 비밀번호 없는 소셜 계정은 본문이 비거나(이메일 확인 링크) confirm_username을
// 쓴다. 이 화면은 비밀번호 계정 기준이고, 나머지는 서버 문장을 그대로 보여준다.
export interface DeleteAccountInput {
  current_password?: string
  confirm_username?: string
}

export function isAccountDeleted(res: AccountDeleteResponse): boolean {
  return res.status === 'deleted'
}

// 서버는 ASCII 주소만 받고 최대 254자다. 보내기 전에 눈에 띄는 오타만 거르고, 최종 판단은 서버(422)에 맡긴다.
export const EMAIL_MAX_LEN = 254
export const PASSWORD_MIN_LEN = 8
export const PASSWORD_MAX_LEN = 128

export function looksLikeEmail(value: string): boolean {
  const v = value.trim()
  return v.length > 0 && v.length <= EMAIL_MAX_LEN && /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(v)
}
