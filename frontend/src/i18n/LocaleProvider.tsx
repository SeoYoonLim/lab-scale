import { useQuery } from '@tanstack/react-query'
import { useEffect, useMemo } from 'react'
import type { ReactNode } from 'react'
import { api } from '../api'
import { DEFAULT_LOCALE } from '../api/contracts/common'
import type { Locale } from '../api/contracts/common'
import { queryKeys } from '../api/queryKeys'
import { useAuth } from '../auth/useAuth'
import { toLocale, translate } from './index'
import { LocaleContext } from './localeContext'
import { setUiLocale } from './uiLocale'

// 로그인 전(로그인·가입·계정 복구 화면)에는 계정 설정을 읽을 수 없으니, 마지막으로 로그인했을 때의 언어를 이 브라우저에 기억해 둔다.
// 처음 방문한 브라우저는 한국어다. 언어는 계정 설정이 기준이고 이 값은 로그아웃 상태의 화면에만 쓴다.
const STORED_LOCALE_KEY = 'ui_locale'

function readStoredLocale(): Locale {
  try {
    return toLocale(localStorage.getItem(STORED_LOCALE_KEY))
  } catch {
    return DEFAULT_LOCALE
  }
}

function writeStoredLocale(locale: Locale): void {
  try {
    localStorage.setItem(STORED_LOCALE_KEY, locale)
  } catch {
    // 저장이 막혀도 이번 세션의 언어에는 영향이 없다
  }
}

// 로그인 직후 한 번 GET /api/auth/preferences로 화면 언어를 정한다(핸드오프 I18N_HISTORY 2-2절). 설정이 바뀌면
// 설정 화면이 이 쿼리의 캐시를 갱신하므로 별도 새로고침이 필요 없다.
export function LocaleProvider({ children }: { children: ReactNode }) {
  const { user } = useAuth()
  const { data } = useQuery({
    queryKey: queryKeys.preferences,
    queryFn: () => api.getPreferences(),
    enabled: user != null,
    staleTime: Infinity,
    retry: false,
  })

  // 설정을 읽기 전(또는 로그아웃 상태)에는 이 브라우저가 마지막으로 쓴 언어를 쓴다
  const locale = user && data ? toLocale(data.preferred_locale) : readStoredLocale()
  setUiLocale(locale)
  const timezone = data?.effective_timezone ?? 'Asia/Seoul'

  useEffect(() => {
    document.documentElement.lang = locale
  }, [locale])

  // 계정 설정을 읽었을 때(또는 설정에서 바꿨을 때) 기억해 둔다. 로그아웃한 뒤 로그인 화면이 같은 언어로 나온다.
  useEffect(() => {
    if (user && data) writeStoredLocale(toLocale(data.preferred_locale))
  }, [user, data])

  const value = useMemo(
    () => ({
      locale,
      timezone,
      preferences: user ? data : undefined,
      t: (key: string, options?: { fallback?: string; vars?: Record<string, string | number> }) =>
        translate(locale, key, options),
    }),
    [locale, timezone, data, user],
  )

  return <LocaleContext.Provider value={value}>{children}</LocaleContext.Provider>
}
