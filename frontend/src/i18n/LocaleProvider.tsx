import { useQuery } from '@tanstack/react-query'
import { useEffect, useMemo } from 'react'
import type { ReactNode } from 'react'
import { api } from '../api'
import { DEFAULT_LOCALE } from '../api/contracts/common'
import { queryKeys } from '../api/queryKeys'
import { useAuth } from '../auth/useAuth'
import { toLocale, translate } from './index'
import { LocaleContext } from './localeContext'

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

  const locale = user && data ? toLocale(data.preferred_locale) : DEFAULT_LOCALE
  const timezone = data?.effective_timezone ?? 'Asia/Seoul'

  useEffect(() => {
    document.documentElement.lang = locale
  }, [locale])

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
