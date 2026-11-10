import type { HistorySearchParams, HistorySearchResponse } from '../contracts/history'

export interface HistoryApi {
  // GET /api/history/search: 리서치·진단 통합 검색(브리핑은 안 나온다). counts가 탭 숫자다.
  searchHistory(params?: HistorySearchParams): Promise<HistorySearchResponse>
}
