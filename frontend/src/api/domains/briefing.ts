import type {
  BriefingHistoryParams,
  DailyBriefing,
  DailyBriefingHistory,
  GenerateBriefingInput,
} from '../contracts/briefing'

export interface BriefingApi {
  // POST /api/briefings/daily/generate. 새로 만들면 created: true(201), 오늘 것이 이미 있으면 저장본 그대로 created: false(200).
  // regenerate: true는 새 버전을 만든다(하루 5개까지, 넘으면 409). AI 요약 때문에 4~15초 걸린다.
  generateDailyBriefing(input?: GenerateBriefingInput): Promise<DailyBriefing>
  // 오늘(시간대 기준) 브리핑의 마지막 버전. 없으면 404가 아니라 null로 돌려준다("만들기" 버튼을 보여줄 상태이지 오류가 아니다).
  getCurrentDailyBriefing(timezone?: string): Promise<DailyBriefing | null>
  listDailyBriefings(params?: BriefingHistoryParams): Promise<DailyBriefingHistory>
  // 저장본 그대로(다시 계산하지 않음). 남의 것·없는 id는 404.
  getDailyBriefing(briefingId: number): Promise<DailyBriefing>
}
