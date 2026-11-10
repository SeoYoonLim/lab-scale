import type { EtfHoldings } from '../contracts/etf'

export interface EtfApi {
  // GET /api/etf/{ticker}/holdings (공개). 구성 데이터가 없는 ETF도 200이고 items가 []다(message로 안내).
  getEtfHoldings(ticker: string, limit?: number): Promise<EtfHoldings>
}
