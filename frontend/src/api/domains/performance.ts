import type { PerformanceParams, PerformanceView } from '../contracts/performance'

export interface PerformanceApi {
  // GET /api/portfolio/performance?view=benchmark. 계산 불가 상태에서도 200이라 status를 먼저 본다.
  getPerformance(params?: PerformanceParams): Promise<PerformanceView>
}
