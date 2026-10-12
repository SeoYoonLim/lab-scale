import { createContext } from 'react'
import type { User } from '../types'

export interface AuthContextValue {
  user: User | null
  // 앱이 막 떴을 때, 저장된 토큰이 유효한지 GET /api/auth/me로 확인하는 동안 true.
  loading: boolean
  login: (username: string, password: string) => Promise<void>
  signup: (username: string, password: string) => Promise<void>
  logout: () => void
}

export const AuthContext = createContext<AuthContextValue | null>(null)
