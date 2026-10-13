import { useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import { api } from '../api'
import { ApiError } from '../api/errors'
import type { User } from '../types'
import { AUTH_EXPIRED_EVENT, clearToken, getToken, setToken } from '../utils/token'
import { AuthContext } from './context'

// Context 객체 자체는 ./context로 분리했다(이 파일이 컴포넌트가 아닌 것도 같이 export하면
// react-refresh/only-export-components가 걸린다). useAuth 훅은 ./useAuth에 있다.
export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null)
  const [loading, setLoading] = useState(true)
  const [initError, setInitError] = useState<string | null>(null)
  const [retryCount, setRetryCount] = useState(0)

  // 앱 시작 시: 저장된 토큰이 있으면 유효한지 확인한다(backend/API.md "프론트가 할 일" 6번).
  // 토큰이 무효해서(401) 실패하면 http.ts/mock.ts가 AUTH_EXPIRED_EVENT를 쏘므로 아래 effect가 user를 비운다.
  // 네트워크/서버 오류로 실패한 거면 토큰 자체는 멀쩡할 수 있으니 로그아웃시키지 않고 initError로 안내한다 -
  // 안 그러면 서버가 잠깐 멈췄을 때도 로그인 안 한 사람처럼 로그인 화면으로 쫓겨난다.
  useEffect(() => {
    let cancelled = false
    async function init() {
      if (!getToken()) {
        setLoading(false)
        return
      }
      setLoading(true)
      setInitError(null)
      try {
        const me = await api.me()
        if (!cancelled) setUser(me)
      } catch (err) {
        if (cancelled) return
        if (!(err instanceof ApiError && err.status === 401)) {
          setInitError('서버에 연결할 수 없어요. 네트워크를 확인하고 다시 시도해주세요.')
        }
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    void init()
    return () => {
      cancelled = true
    }
  }, [retryCount])

  function retryInit() {
    setRetryCount((c) => c + 1)
  }

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

  return (
    <AuthContext.Provider value={{ user, loading, initError, retryInit, login, signup, logout }}>
      {children}
    </AuthContext.Provider>
  )
}
