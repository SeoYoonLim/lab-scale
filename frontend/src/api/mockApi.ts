import type { Api } from './index'
import { baseMockApi } from './mock'
import { briefingMock } from './mock-briefing'
import { etfMock, exposureMock } from './mock-exposure'
import { historyMock } from './mock-history'
import { performanceMock } from './mock-performance'

// 영역별 mock을 한 객체로 합친다. 새 영역을 추가할 때는 mock-*.ts 파일을 만들고 여기에 펼친다.
// (mock.ts는 예전부터 있던 영역과 공용 저장소를 가지고 있고, 영역별 파일이 거기서 storeFor/currentUserId를 가져다 쓴다.)
export const mockApi: Api = {
  ...baseMockApi,
  ...performanceMock,
  ...exposureMock,
  ...etfMock,
  ...briefingMock,
  ...historyMock,
}
