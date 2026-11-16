import type { ReactNode } from 'react'

// 가로로 스크롤되는 표를 감싼다. 좁은 화면에서 표가 넘치면 마우스·터치로는 밀어 볼 수 있지만, 키보드 사용자는
// 스크롤 영역에 초점이 갈 수 없으면 가려진 열을 볼 수 없다. 그래서 초점을 받게 하고(tabIndex=0), 화면 읽기 프로그램이
// "무슨 영역인지" 알 수 있게 이름을 붙인다(role=region + aria-label).
export default function ScrollRegion({ label, className, children }: { label: string; className: string; children: ReactNode }) {
  return (
    <div className={className} role="region" aria-label={label} tabIndex={0}>
      {children}
    </div>
  )
}
