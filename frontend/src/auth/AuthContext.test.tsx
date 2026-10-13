import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { AuthProvider } from './AuthContext'
import { useAuth } from './useAuth'
import { ApiError } from '../api/errors'
import { setToken } from '../utils/token'

// AuthContext는 api.me()로 저장된 토큰을 검증한다 - 이 모듈을 통째로 mock해서 그 호출 결과를 테스트마다 바꾼다.
vi.mock('../api', () => ({
  api: {
    me: vi.fn(),
    login: vi.fn(),
    signup: vi.fn(),
  },
}))

// 모킹된 모듈이라 실제 타입과는 안 맞지만, 테스트에서 mockResolvedValue/mockRejectedValue를 걸려면
// 구체적인 vi.fn() 핸들이 필요하다.
const { api } = await import('../api')
const mockMe = api.me as unknown as ReturnType<typeof vi.fn>

function Probe() {
  const { user, loading, initError, retryInit } = useAuth()
  return (
    <div>
      <span data-testid="loading">{String(loading)}</span>
      <span data-testid="user">{user ? user.username : 'none'}</span>
      <span data-testid="initError">{initError ?? 'none'}</span>
      <button onClick={retryInit}>retry</button>
    </div>
  )
}

function renderProbe() {
  return render(
    <AuthProvider>
      <Probe />
    </AuthProvider>,
  )
}

describe('AuthProvider 초기화', () => {
  it('저장된 토큰이 없으면 api.me를 부르지 않고 바로 로딩을 끝낸다', async () => {
    renderProbe()
    await waitFor(() => expect(screen.getByTestId('loading').textContent).toBe('false'))
    expect(mockMe).not.toHaveBeenCalled()
    expect(screen.getByTestId('user').textContent).toBe('none')
  })

  it('api.me가 401(ApiError)로 실패하면 initError 없이 조용히 비로그인 상태로 둔다(AUTH_EXPIRED_EVENT가 처리)', async () => {
    setToken('token-for-401-test')
    mockMe.mockRejectedValueOnce(new ApiError('인증이 필요합니다.', 401))

    renderProbe()
    await waitFor(() => expect(screen.getByTestId('loading').textContent).toBe('false'))
    expect(screen.getByTestId('user').textContent).toBe('none')
    expect(screen.getByTestId('initError').textContent).toBe('none')
  })

  it('api.me가 네트워크 오류(ApiError 아님)로 실패하면 initError를 보여준다 - 401과 달리 로그아웃시키지 않는다', async () => {
    setToken('token-for-network-error-test')
    mockMe.mockRejectedValueOnce(new TypeError('Failed to fetch'))

    renderProbe()
    await waitFor(() => expect(screen.getByTestId('loading').textContent).toBe('false'))
    expect(screen.getByTestId('user').textContent).toBe('none')
    expect(screen.getByTestId('initError').textContent).not.toBe('none')
  })

  it('retryInit을 부르면 api.me를 다시 호출해서 회복할 수 있다', async () => {
    setToken('token-for-retry-test')
    mockMe.mockRejectedValueOnce(new TypeError('Failed to fetch'))
    mockMe.mockResolvedValueOnce({ id: 1, username: 'seoyoon' })

    renderProbe()
    await waitFor(() => expect(screen.getByTestId('initError').textContent).not.toBe('none'))

    fireEvent.click(screen.getByText('retry'))
    await waitFor(() => expect(screen.getByTestId('user').textContent).toBe('seoyoon'))
    expect(screen.getByTestId('initError').textContent).toBe('none')
    expect(mockMe).toHaveBeenCalledTimes(2)
  })
})
