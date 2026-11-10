import type { Locale } from '../api/contracts/common'
import { EXTRA_EN } from './extra.en'
import { EXTRA_JA } from './extra.ja'
import { EXTRA_ZH } from './extra.zh'

// 백엔드 리소스(docs/i18n/*.json)에 없는 화면 라벨의 언어별 번역(영어·일본어·중국어 간체). 한국어는 코드 안의 fallback이 원문이다
// (t(key, { fallback: '한국어' })). 찾는 순서는 index.ts의 translate()를 본다. 세 언어의 키가 같아야 하고, 화면이 쓰는 키마다
// 번역이 있어야 한다(coverage.test.ts가 확인한다).
export const EXTRA_MESSAGES: Partial<Record<Locale, Record<string, string>>> = {
  'en-US': EXTRA_EN,
  'ja-JP': EXTRA_JA,
  'zh-CN': EXTRA_ZH,
}
