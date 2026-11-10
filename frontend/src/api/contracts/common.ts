import { z } from 'zod'

// 새 백엔드(2026-11)의 여러 응답이 같이 쓰는 값들. 상태값 합(union)은 백엔드 상수와 같다
// (docs/FRONTEND_INTEGRATION_HANDOFF.md 11절, 백엔드 통합 테스트가 확인).

export const LOCALES = ['ko-KR', 'en-US', 'ja-JP', 'zh-CN'] as const
export type Locale = (typeof LOCALES)[number]
export const DEFAULT_LOCALE: Locale = 'ko-KR'

// 응답의 locale은 "실제로 답한 언어"다. 지원하지 않는 값을 보내면 ko-KR로 오지만, 문자열로 받아두고
// 화면에서 isLocale()로 좁혀 쓴다(서버가 나중에 언어를 늘려도 응답 검증이 깨지지 않게).
export function isLocale(value: unknown): value is Locale {
  return typeof value === 'string' && (LOCALES as readonly string[]).includes(value)
}

// 번역 상태(15-1절). null = ko-KR 답(번역 안 함).
export const TRANSLATION_STATUSES = ['localized', 'translated', 'fallback_summary', 'fallback_original'] as const
export type TranslationStatus = (typeof TRANSLATION_STATUSES)[number]

export const translationStatusSchema = z.enum(TRANSLATION_STATUSES)

// 기록 목록의 날짜 그룹(시간대 기준 오늘부터의 날짜 차이)
export const DATE_GROUPS = ['today', 'yesterday', 'last_7_days', 'last_30_days', 'older'] as const
export type DateGroup = (typeof DATE_GROUPS)[number]
export const dateGroupSchema = z.enum(DATE_GROUPS)

// 리서치 분석 유형(기록 배지)
export const ANALYSIS_TYPES = [
  'outlook',
  'event',
  'briefing',
  'discovery',
  'etf',
  'disclosure',
  'news',
  'price',
  'market',
  'general',
] as const
export type AnalysisType = (typeof ANALYSIS_TYPES)[number]
export const analysisTypeSchema = z.enum(ANALYSIS_TYPES)

export type MarketDataStatus = 'latest' | 'delayed' | 'stale' | 'no_data'
export type SectionStatus = 'ok' | 'partial' | 'no_data' | 'empty'

// limit/offset, page/size 목록이 공통으로 주는 페이지 정보(핸드오프 9절). 범위를 넘는 페이지는 오류가 아니라
// items: [] + has_next: false다.
export interface PageInfo {
  total: number
  has_next: boolean
  total_pages: number
}

export const pageInfoShape = {
  total: z.number(),
  has_next: z.boolean(),
  total_pages: z.number(),
}
