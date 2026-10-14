import type { AssetType, TrendingCategory } from '../types'

// 화면마다 따로 캐시 키를 짓지 않도록 한곳에 모았다. 뮤테이션(주문/초기화/관심종목 추가·삭제) 성공 뒤
// queryClient.invalidateQueries로 무효화할 대상도 이 키를 그대로 쓴다 - 배열 앞부분만 맞으면(예:
// ['trades'])  오프셋/필터가 다른 모든 trades 쿼리가 한꺼번에 무효화된다.
export const queryKeys = {
  portfolio: ['portfolio'] as const,
  watchlist: ['watchlist'] as const,
  trades: (limit: number, offset: number, ticker?: string) => ['trades', { limit, offset, ticker }] as const,
  reports: (limit: number, offset: number) => ['reports', { limit, offset }] as const,
  report: (reportId: number) => ['report', reportId] as const,
  companies: (q: string | undefined, limit: number, assetType?: AssetType) =>
    ['companies', { q, limit, assetType }] as const,
  realtimePrice: (ticker: string) => ['realtimePrice', ticker] as const,
  trending: (category: TrendingCategory, assetType: AssetType = 'stock') =>
    ['trending', category, assetType] as const,
  disclaimer: ['disclaimer'] as const,
  // 진단 이력(2026-10-14 추가). diagnosisHistory는 부분 키라서 삭제 뮤테이션이 limit/offset 상관없이
  // ['diagnosisHistory']로 한꺼번에 무효화할 수 있다.
  diagnosisHistory: (limit: number, offset: number) => ['diagnosisHistory', { limit, offset }] as const,
  diagnosisDetail: (diagnosisId: number) => ['diagnosisDetail', diagnosisId] as const,
  diagnosisCompare: (diagnosisId: number, baseId?: number) => ['diagnosisCompare', diagnosisId, baseId] as const,
}
