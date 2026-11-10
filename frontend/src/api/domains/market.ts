import type { MarketOverview } from '../contracts/market'

export interface MarketApi {
  // GET /api/market/overview (공개). 데이터가 없어도 200이고 status로 구분한다.
  getMarketOverview(): Promise<MarketOverview>
}
