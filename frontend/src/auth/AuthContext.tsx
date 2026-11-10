import { useQueryClient } from '@tanstack/react-query'
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
  // 로그인 화면이 보여줄 안내(회원 탈퇴 완료 등). 라우터 state로 넘기면 로그인 상태를 비우는 순간 RequireAuth의 이동이 덮어써서
  // 사라지므로 여기에 둔다.
  const [notice, setNotice] = useState<string | null>(null)
  // 쿼리 캐시 키에는 사용자 구분이 없어서(['portfolio'] 등), 로그아웃·토큰 만료·계정 전환 때 비우지 않으면 다음 사람이
  // 이전 사용자의 포트폴리오·기록·브리핑을 잠깐 보게 된다.
  const queryClient = useQueryClient()

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
      queryClient.clear()
      setUser(null)
    }
    window.addEventListener(AUTH_EXPIRED_EVENT, handleExpired)
    return () => window.removeEventListener(AUTH_EXPIRED_EVENT, handleExpired)
  }, [queryClient])

  async function login(username: string, password: string) {
    const res = await api.login(username, password)
    queryClient.clear()
    setToken(res.access_token)
    setNotice(null)
    setUser(res.user)
  }

  async function signup(username: string, password: string) {
    const res = await api.signup(username, password)
    queryClient.clear()
    setToken(res.access_token)
    setNotice(null)
    setUser(res.user)
  }

  function logout() {
    // 서버에도 알려 이 사용자의 토큰을 폐기하게 한다(2026-10-30 POST /api/auth/logout, 전체 기기 로그아웃).
    // 화면은 서버 응답을 기다리지 않고 바로 로그아웃한다 - 요청의 Authorization 헤더는 api.logout()을 부르는 이 순간
    // 동기적으로 만들어지므로 바로 아래에서 토큰을 지워도 이미 실린 상태다. 서버 호출이 실패해도(네트워크·이미 만료)
    // 이 기기에서는 로그아웃된 것이고, 토큰은 24시간 뒤 어차피 만료된다.
    void api.logout().catch(() => undefined)
    clearToken()
    queryClient.clear()
    setUser(null)
  }

  // 회원 탈퇴가 끝났을 때: 계정이 없으니 서버 로그아웃을 부르면 401만 난다. 이 기기의 토큰·캐시·로그인 상태만 비운다.
  function endSession(message?: string) {
    clearToken()
    queryClient.clear()
    setNotice(message ?? null)
    setUser(null)
  }

  return (
    <AuthContext.Provider value={{ user, loading, initError, retryInit, login, signup, logout, endSession, notice }}>
      {children}
    </AuthContext.Provider>
  )
}
