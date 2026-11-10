import { useContext } from 'react'
import { DEFAULT_LOCALE } from '../api/contracts/common'
import { translate } from './index'
import { LocaleContext } from './localeContext'
import type { LocaleContextValue } from './localeContext'

// Provider 밖(테스트 등)에서도 죽지 않게 한국어 기본값을 돌려준다.
const FALLBACK: LocaleContextValue = {
  locale: DEFAULT_LOCALE,
  timezone: 'Asia/Seoul',
  preferences: undefined,
  t: (key, options) => translate(DEFAULT_LOCALE, key, options),
}

export function useLocale(): LocaleContextValue {
  return useContext(LocaleContext) ?? FALLBACK
}
