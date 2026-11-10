import { useQuery } from '@tanstack/react-query'
import { api } from '../api'
import { queryKeys } from '../api/queryKeys'
import { formatDate } from '../utils/format'

// GET /api/disclaimer + GET /api/status. 로그인 여부와 무관하게 앱 하단에 항상 보여준다(backend/API.md 4번 요청사항).
export default function DisclaimerFooter() {
  // 면책 문구는 세션 중에 안 바뀌니 staleTime: Infinity로 두면 화면을 옮겨다녀도 다시 안 부른다.
  const { data } = useQuery({ queryKey: queryKeys.disclaimer, queryFn: () => api.getDisclaimer(), staleTime: Infinity })
  // 데이터 기준일은 하루 단위로 바뀌는 값이라 5분 캐시면 충분하다. 실패해도 푸터의 나머지(면책 문구)는
  // 그대로 보여야 해서 에러는 따로 표시하지 않고 기준일 줄만 빠진다.
  const { data: status } = useQuery({
    queryKey: queryKeys.status,
    queryFn: () => api.getStatus(),
    staleTime: 5 * 60_000,
    retry: false,
  })
  if (!data && !status) return null

  const stale = status?.sources.market_index.stale
  const refreshFailed = status?.last_refresh != null && !status.last_refresh.ok
  return (
    <footer className="app-footer muted small">
      {status && (
        <p className="data-basis">
          <span>
            데이터 기준일 <span className="num">{formatDate(status.sources.stock_price.latest_date)}</span> 종가
          </span>
          {stale && <span className="data-basis-warn">시장 지수 데이터가 늦어지고 있어요</span>}
          {refreshFailed && <span className="data-basis-warn">마지막 데이터 갱신에 실패한 단계가 있어요</span>}
        </p>
      )}
      {data && <p>{data}</p>}
    </footer>
  )
}
