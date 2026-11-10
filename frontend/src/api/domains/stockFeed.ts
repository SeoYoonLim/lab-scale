import type { DisclosureParams, FeedParams, StockDisclosures, StockNews } from '../contracts/stockFeed'

export interface StockFeedApi {
  // GET /api/stocks/{ticker}/news (공개). 종목을 못 찾으면 404, 날짜 범위가 거꾸로면 422.
  getStockNews(ticker: string, params?: FeedParams): Promise<StockNews>
  // GET /api/stocks/{ticker}/disclosures (공개). ETF는 200 + supported=false + message다.
  getStockDisclosures(ticker: string, params?: DisclosureParams): Promise<StockDisclosures>
}
