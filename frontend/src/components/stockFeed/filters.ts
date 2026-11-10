import type { FeedOrder } from '../../api/contracts/stockFeed'

// 종목 뉴스·공시 카드가 같이 쓰는 필터(기간·정렬) 상태
export interface FeedFilterState {
  dateFrom: string
  dateTo: string
  order: FeedOrder
}

export const DEFAULT_FILTERS: FeedFilterState = { dateFrom: '', dateTo: '', order: 'desc' }

export function filtersActive(f: FeedFilterState): boolean {
  return f.dateFrom !== '' || f.dateTo !== '' || f.order !== DEFAULT_FILTERS.order
}
