import type { ReactNode } from 'react'

export type BadgeTone = 'neutral' | 'ok' | 'warn' | 'danger' | 'accent'

// 상태·유형을 작은 알약으로 보여준다. 색은 보조일 뿐이라 항상 글자가 같이 들어간다.
export default function StatusBadge({ tone = 'neutral', children }: { tone?: BadgeTone; children: ReactNode }) {
  return <span className={`status-badge status-badge-${tone}`}>{children}</span>
}
