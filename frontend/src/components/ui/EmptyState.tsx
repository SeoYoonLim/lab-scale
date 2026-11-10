import type { ReactNode } from 'react'

// "데이터 없음"은 오류가 아니라 정상 상태다(핸드오프 8절). 무엇이 없는지와 다음에 할 일을 같이 보여준다.
export default function EmptyState({
  message,
  action,
}: {
  message: ReactNode
  action?: ReactNode
}) {
  return (
    <div className="empty-state">
      <p className="muted">{message}</p>
      {action && <div className="empty-state-action">{action}</div>}
    </div>
  )
}
