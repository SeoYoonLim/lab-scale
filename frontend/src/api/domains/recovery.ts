import type { AuthResponse } from '../../types'
import type { AccountDeleteResponse, DeleteAccountInput, EmailStatus, MessageResponse } from '../contracts/account'

// 이메일 등록·인증, 아이디/비밀번호 찾기·재설정, 비밀번호 변경. 메일 링크의 토큰은 주소의 fragment(#token=)로 온다.
export interface RecoveryApi {
  // PUT /api/auth/email (인증). 현재 비밀번호가 필수다(처음 등록할 때도). 바꾸면 인증 전 상태로 돌아가고 인증 메일이 간다.
  setEmail(email: string, currentPassword: string): Promise<EmailStatus>
  // POST /api/auth/resend-verification (인증). 이전 인증 링크는 무효가 된다.
  resendVerification(): Promise<MessageResponse>
  // POST /api/auth/verify-email (공개). 토큰은 1회용이다.
  verifyEmail(token: string): Promise<MessageResponse>
  // POST /api/auth/find-username, /forgot-password (공개). 이메일이 있든 없든 같은 200이다(계정 존재 여부를 숨긴다).
  findUsername(email: string): Promise<MessageResponse>
  forgotPassword(email: string): Promise<MessageResponse>
  // POST /api/auth/reset-password (공개). 성공하면 그 계정의 모든 기기가 로그아웃된다(토큰을 주지 않는다).
  resetPassword(token: string, newPassword: string): Promise<MessageResponse>
  // POST /api/auth/change-password (인증). 기존 토큰이 전부 무효가 되므로 응답의 새 토큰으로 바꿔 저장해야 한다.
  changePassword(currentPassword: string, newPassword: string): Promise<AuthResponse>
  // DELETE /api/auth/me (인증). 계정과 모든 데이터를 지운다(되돌릴 수 없음). 비밀번호 계정은 200 deleted, 소셜 계정은 202 confirmation_sent일 수 있다.
  deleteAccount(input: DeleteAccountInput): Promise<AccountDeleteResponse>
  // POST /api/auth/delete-account/confirm (공개). 탈퇴 확인 메일 링크의 토큰으로 확정한다(1회용, 30분).
  confirmDeleteAccount(token: string): Promise<AccountDeleteResponse>
}
