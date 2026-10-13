import { renderHook, waitFor } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { useAsync } from './useAsync'

// resolve()를 밖에서 호출할 수 있는 Promise를 만든다 - 응답이 "아직 안 끝난" 상태를 테스트에서 붙잡아 두기 위해.
function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((r) => {
    resolve = r
  })
  return { promise, resolve }
}

describe('useAsync', () => {
  it('첫 로딩에는 loading=true, refetching=false이고, 응답이 오면 data가 채워진다', async () => {
    const { promise, resolve } = deferred<string>()
    const { result } = renderHook(() => useAsync(() => promise, []))

    expect(result.current.loading).toBe(true)
    expect(result.current.refetching).toBe(false)

    resolve('hello')
    await waitFor(() => expect(result.current.data).toBe('hello'))
    expect(result.current.loading).toBe(false)
  })

  it('이전 응답이 늦게 와도(취소된 effect) 최신 상태를 덮어쓰지 않는다', async () => {
    const first = deferred<string>()
    const second = deferred<string>()
    let call = 0

    const { result, rerender } = renderHook(({ dep }: { dep: number }) => useAsync(() => (call++ === 0 ? first.promise : second.promise), [dep]), {
      initialProps: { dep: 1 },
    })

    rerender({ dep: 2 })
    // dep=2로 넘어간 뒤에야 dep=1 때 보냈던(이제는 취소된) 첫 요청이 응답한다.
    first.resolve('stale response from dep=1')
    second.resolve('fresh response from dep=2')

    await waitFor(() => expect(result.current.data).toBe('fresh response from dep=2'))
    expect(result.current.data).not.toBe('stale response from dep=1')
  })

  it('데이터가 있는 상태에서 deps가 바뀌면 refetching=true이고, 이전 데이터는 그대로 보여준다', async () => {
    const first = deferred<string>()
    const second = deferred<string>()
    let call = 0

    const { result, rerender } = renderHook(({ dep }: { dep: number }) => useAsync(() => (call++ === 0 ? first.promise : second.promise), [dep]), {
      initialProps: { dep: 1 },
    })

    first.resolve('first')
    await waitFor(() => expect(result.current.data).toBe('first'))

    rerender({ dep: 2 })
    // 두 번째 요청이 아직 안 끝난 동안: 이전 데이터("first")는 남아있고 refetching만 true.
    expect(result.current.data).toBe('first')
    expect(result.current.refetching).toBe(true)

    second.resolve('second')
    await waitFor(() => expect(result.current.data).toBe('second'))
    expect(result.current.refetching).toBe(false)
  })
})
