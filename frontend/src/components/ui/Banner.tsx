import type { ReactNode } from 'react'

export type BannerTone = 'info' | 'warn' | 'danger'

// 데이터 지연·오래된 구성·부분 데이터 같은 "숫자를 믿기 전에 알아야 할 것"을 화면 맨 위에 띄우는 띠.
// 색에만 의존하지 않도록 아이콘 글자와 role을 같이 둔다(warn/danger는 role="alert" 대신 status로 읽혀 화면 읽기를 끊지 않게 한다).
const ICON: Record<BannerTone, string> = { info: 'i', warn: '!', danger: '!' }

export default function Banner({
  tone = 'info',
  title,
  children,
}: {
  tone?: BannerTone
  title?: string
  children?: ReactNode
}) {
  return (
    <div className={`banner banner-${tone}`} role={tone === 'danger' ? 'alert' : 'status'}>
      <span className="banner-icon" aria-hidden="true">
        {ICON[tone]}
      </span>
      <div className="banner-body">
        {title && <strong className="banner-title">{title}</strong>}
        {children && <div className="banner-text">{children}</div>}
      </div>
    </div>
  )
}
