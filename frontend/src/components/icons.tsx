import type { ReactNode } from 'react'

// 아이콘 라이브러리 없이 쓰는 24x24 선 아이콘 모음. 장식용이라 스크린리더에서는 숨긴다.
function Icon({ children, size = 20 }: { children: ReactNode; size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      {children}
    </svg>
  )
}

export const IconHome = () => (
  <Icon>
    <path d="M3 11.5 12 4l9 7.5" />
    <path d="M5.5 10v9.5h13V10" />
  </Icon>
)

export const IconChart = () => (
  <Icon>
    <path d="M4 19V5" />
    <path d="M4 19h16" />
    <path d="m7.5 14 3-3.5 3 2L19 7" />
  </Icon>
)

export const IconFile = () => (
  <Icon>
    <path d="M7 3.5h7l4 4V20.5H7z" />
    <path d="M14 3.5V8h4" />
    <path d="M9.5 13h5M9.5 16.5h5" />
  </Icon>
)

export const IconWallet = () => (
  <Icon>
    <path d="M4 7.5h14.5a1.5 1.5 0 0 1 1.5 1.5v9a1.5 1.5 0 0 1-1.5 1.5h-13A1.5 1.5 0 0 1 4 18z" />
    <path d="M4 7.5 15.5 4.5v3" />
    <circle cx="16" cy="13.5" r="1" />
  </Icon>
)

export const IconSend = () => (
  <Icon>
    <path d="m21 3-9.5 18-2.2-7.8L2 11z" />
    <path d="m21 3-11.7 10.2" />
  </Icon>
)

export const IconSparkle = () => (
  <Icon>
    <path d="M12 3.5 13.9 9l5.6 1.9-5.6 1.9L12 18.3l-1.9-5.5L4.5 10.9 10.1 9z" />
    <path d="M19 3v3M17.5 4.5h3" />
  </Icon>
)

export const IconBulb = () => (
  <Icon>
    <path d="M9 18h6M10 21h4" />
    <path d="M12 3.5a6 6 0 0 0-3.6 10.8c.7.6 1.1 1.3 1.1 2.2h5c0-.9.4-1.6 1.1-2.2A6 6 0 0 0 12 3.5z" />
  </Icon>
)

export const IconNews = () => (
  <Icon>
    <path d="M5 5h12v14H6.5A1.5 1.5 0 0 1 5 17.5z" />
    <path d="M17 9h2.5v8.5a1.5 1.5 0 0 1-1.5 1.5H17" />
    <path d="M8 9h6M8 12h6M8 15h4" />
  </Icon>
)

export const IconGlobe = () => (
  <Icon>
    <circle cx="12" cy="12" r="8.5" />
    <path d="M3.5 12h17" />
    <path d="M12 3.5c2.5 2.6 3.7 5.4 3.7 8.5s-1.2 5.9-3.7 8.5c-2.5-2.6-3.7-5.4-3.7-8.5S9.5 6.1 12 3.5z" />
  </Icon>
)

export const IconSearch = () => (
  <Icon>
    <circle cx="11" cy="11" r="6.5" />
    <path d="m20 20-4.3-4.3" />
  </Icon>
)
