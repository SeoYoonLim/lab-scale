import { DEFAULT_LOCALE } from '../api/contracts/common'
import type { Locale } from '../api/contracts/common'

// 화면 언어를 컴포넌트 밖의 순수 함수(utils/format.ts의 formatWon 등)도 알 수 있게 모듈에 하나 둔다. LocaleProvider가 언어가 정해질
// 때(렌더 중) 갱신하고, 언어가 바뀌면 Provider가 다시 렌더돼 이 값을 쓰는 화면도 같이 다시 그려진다.
let current: Locale = DEFAULT_LOCALE

export function setUiLocale(locale: Locale): void {
  current = locale
}

export function getUiLocale(): Locale {
  return current
}
