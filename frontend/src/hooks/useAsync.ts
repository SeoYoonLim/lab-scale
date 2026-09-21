import { useEffect, useState } from 'react'

interface AsyncState<T> {
  data: T | undefined
  error: Error | undefined
  loading: boolean
}

// deps 가 바뀔 때마다 fn 을 다시 실행한다. 이전 요청의 늦은 응답은 무시한다.
export function useAsync<T>(fn: () => Promise<T>, deps: unknown[]): AsyncState<T> {
  const [state, setState] = useState<AsyncState<T>>({
    data: undefined,
    error: undefined,
    loading: true,
  })

  // deps 는 호출하는 쪽에서 넘기는 값이라 정적으로 검사할 수 없다.
  // oxlint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => {
    let cancelled = false
    setState((prev) => ({ ...prev, error: undefined, loading: true }))
    fn().then(
      (data) => {
        if (!cancelled) setState({ data, error: undefined, loading: false })
      },
      (error: unknown) => {
        if (cancelled) return
        const err = error instanceof Error ? error : new Error(String(error))
        setState({ data: undefined, error: err, loading: false })
      },
    )
    return () => {
      cancelled = true
    }
    // oxlint-disable-next-line react-hooks/exhaustive-deps
  }, deps)

  return state
}
