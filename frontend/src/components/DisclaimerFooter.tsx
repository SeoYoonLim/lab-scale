import { api } from '../api'
import { useAsync } from '../hooks/useAsync'

// GET /api/disclaimer. 로그인 여부와 무관하게 앱 하단에 항상 보여준다(backend/API.md 4번 요청사항).
export default function DisclaimerFooter() {
  const { data } = useAsync(() => api.getDisclaimer(), [])
  if (!data) return null
  return <p className="app-footer muted small">{data}</p>
}
