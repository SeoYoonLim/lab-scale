import { createContext } from 'react'
import type { User } from '../types'

export interface AuthContextValue {
  user: User | null
  // 앱이 막 떴을 때, 저장된 토큰이 유효한지 GET /api/auth/me로 확인하는 동안 true.
  loading: boolean
  // 그 확인이 401이 아닌 이유로(네트워크 끊김, 서버 다운 등) 실패했을 때의 안내 문구.
  // 401이면 토큰이 진짜 무효한 거라 AUTH_EXPIRED_EVENT가 로그아웃시키지만, 그 외에는 토큰이
  // 멀쩡할 수 있으니 로그아웃시키지 않고 이걸로 재시도를 안내한다.
  initError: string | null
  retryInit: () => void
  login: (username: string, password: string) => Promise<void>
  signup: (username: string, password: string) => Promise<void>
  logout: () => void
}

export const AuthContext = createContext<AuthContextValue | null>(null)
