import type { ExposureParams, LookThroughView } from '../contracts/exposure'

export interface ExposureApi {
  // GET /api/portfolio/exposure?view=look_through. 새 사용자·보유 없음도 200(status: empty)이다.
  getLookThroughExposure(params?: ExposureParams): Promise<LookThroughView>
}
