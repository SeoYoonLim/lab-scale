import { createContext, useContext, useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import { api } from '../api'
import type { User } from '../types'
import { AUTH_EXPIRED_EVENT, clearToken, getToken, setToken } from '../utils/token'

interface AuthContextValue {
  user: User | null
  // 앱이 막 떴을 때, 저장된 토큰이 유효한지 GET /api/auth/me로 확인하는 동안 true.
  loading: boolean
  login: (username: string, password: string) => Promise<void>
  signup: (username: string, password: string) => Promise<void>
  logout: () => void
}

const AuthContext = createContext<AuthContextValue | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null)
  const [loading, setLoading] = useState(true)

  // 앱 시작 시: 저장된 토큰이 있으면 유효한지 확인한다(backend/API.md "프론트가 할 일" 6번).
  // 토큰이 없거나 무효하면(401) http.ts/mock.ts가 AUTH_EXPIRED_EVENT를 쏘므로 아래 effect가 user를 비운다.
  useEffect(() => {
    let cancelled = false
    async function init() {
      if (!getToken()) {
        setLoading(false)
        return
      }
      try {
        const me = await api.me()
        if (!cancelled) setUser(me)
      } catch {
        // 401 처리는 AUTH_EXPIRED_EVENT 리스너가 한다.
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    void init()
    return () => {
      cancelled = true
    }
  }, [])

  // 어떤 API 호출이든 401을 받으면(토큰 만료/변조 포함) 로그인 상태를 비운다.
  // RequireAuth가 user===null을 보고 로그인 화면으로 돌려보낸다.
  useEffect(() => {
    function handleExpired() {
      setUser(null)
    }
    window.addEventListener(AUTH_EXPIRED_EVENT, handleExpired)
    return () => window.removeEventListener(AUTH_EXPIRED_EVENT, handleExpired)
  }, [])

  async function login(username: string, password: string) {
    const res = await api.login(username, password)
    setToken(res.access_token)
    setUser(res.user)
  }

  async function signup(username: string, password: string) {
    const res = await api.signup(username, password)
    setToken(res.access_token)
    setUser(res.user)
  }

  function logout() {
    // 서버에 토큰을 폐기하는 API는 없다(backend/API.md "인증 관련 한계") — 저장된 토큰을 지우는 게 전부다.
    clearToken()
    setUser(null)
  }

  return <AuthContext.Provider value={{ user, loading, login, signup, logout }}>{children}</AuthContext.Provider>
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth는 AuthProvider 안에서만 쓸 수 있어요.')
  return ctx
}
