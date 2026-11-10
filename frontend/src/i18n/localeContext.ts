import { createContext } from 'react'
import type { Locale } from '../api/contracts/common'
import type { Preferences } from '../api/contracts/preferences'

export interface LocaleContextValue {
  // 화면 라벨과 날짜·숫자 표시에 쓰는 언어. 로그인 전·설정 로딩 중에는 ko-KR.
  locale: Locale
  // 날짜·시각을 보여줄 시간대(계정 설정, 없으면 Asia/Seoul)
  timezone: string
  preferences: Preferences | undefined
  // 라벨 번역(리소스에 없으면 한국어 → fallback → 키). 문장(답변·경고·요약)은 API가 그 언어로 주므로 이걸 쓰지 않는다.
  t: (key: string, options?: { fallback?: string; vars?: Record<string, string | number> }) => string
}

export const LocaleContext = createContext<LocaleContextValue | null>(null)
