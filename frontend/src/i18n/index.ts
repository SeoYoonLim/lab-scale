import { DEFAULT_LOCALE, isLocale } from '../api/contracts/common'
import type { Locale } from '../api/contracts/common'
import { EXTRA_MESSAGES } from './extra'
import enUS from './en-US.json'
import jaJP from './ja-JP.json'
import koKR from './ko-KR.json'
import zhCN from './zh-CN.json'

// 백엔드가 준 화면 라벨 리소스(docs/i18n/{locale}.json, 4개 언어가 같은 키)를 그대로 복사해 쓴다.
// 문장(답변·경고·요약)은 API가 그 언어로 만들어 주므로 여기 없다 - 이 파일은 버튼·배지·열 이름 같은 라벨 전용이다.
// 새 파일을 받으면 이 폴더의 JSON만 바꾸면 된다(키 일치는 i18n.test.ts가 확인한다).
export type Messages = typeof koKR

export const MESSAGES: Record<Locale, Messages> = {
  'ko-KR': koKR,
  'en-US': enUS,
  'ja-JP': jaJP,
  'zh-CN': zhCN,
}

// 언어 선택 목록은 "각 언어의 자기 이름"으로 보여줘야 해서 직접 둔다. (리소스의 locale_name은 파일마다 그 파일 언어의
// 이름으로만 채워져 있어서 - 영어 파일의 locale_name은 네 개 다 "English" - 선택 목록에 쓸 수 없다.)
export const LOCALE_NATIVE_NAMES: Record<Locale, string> = {
  'ko-KR': '한국어',
  'en-US': 'English',
  'ja-JP': '日本語',
  'zh-CN': '简体中文',
}

function lookup(messages: unknown, path: string): string | undefined {
  let node: unknown = messages
  for (const part of path.split('.')) {
    if (node == null || typeof node !== 'object') return undefined
    node = (node as Record<string, unknown>)[part]
  }
  return typeof node === 'string' ? node : undefined
}

// "{page} / {total_pages}" 같은 자리표시자를 채운다. 값이 없으면 자리표시자를 그대로 둔다(빈 칸이 되지 않게).
export function interpolate(template: string, vars?: Record<string, string | number>): string {
  if (!vars) return template
  return template.replace(/\{(\w+)\}/g, (whole, name: string) => (name in vars ? String(vars[name]) : whole))
}

// 찾는 순서: 현재 언어 리소스 -> 현재 언어 추가 라벨 -> (한국어가 아니면) 영어 추가 라벨 -> 한국어 리소스 -> fallback(코드에
// 적어 둔 한국어) -> 키 그대로. 라벨이 빈 칸으로 사라지는 것보다 어색한 언어라도 보이는 편이 낫다.
export function translate(
  locale: Locale,
  key: string,
  options: { fallback?: string; vars?: Record<string, string | number> } = {},
): string {
  const raw =
    lookup(MESSAGES[locale], key) ??
    EXTRA_MESSAGES[locale]?.[key] ??
    (locale === DEFAULT_LOCALE ? undefined : EXTRA_MESSAGES['en-US']?.[key]) ??
    lookup(MESSAGES[DEFAULT_LOCALE], key) ??
    options.fallback ??
    key
  return interpolate(raw, options.vars)
}

export function toLocale(value: unknown): Locale {
  return isLocale(value) ? value : DEFAULT_LOCALE
}
