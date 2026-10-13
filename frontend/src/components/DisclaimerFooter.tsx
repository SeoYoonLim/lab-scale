import { useQuery } from '@tanstack/react-query'
import { api } from '../api'
import { queryKeys } from '../api/queryKeys'

// GET /api/disclaimer. 로그인 여부와 무관하게 앱 하단에 항상 보여준다(backend/API.md 4번 요청사항).
export default function DisclaimerFooter() {
  // 면책 문구는 세션 중에 안 바뀌니 staleTime: Infinity로 두면 화면을 옮겨다녀도 다시 안 부른다.
  const { data } = useQuery({ queryKey: queryKeys.disclaimer, queryFn: () => api.getDisclaimer(), staleTime: Infinity })
  if (!data) return null
  return <p className="app-footer muted small">{data}</p>
}
