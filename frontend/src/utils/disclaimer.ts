// 답 본문 끝 문단이 응답의 면책 문구(disclaimer)와 정확히 같으면 본문에서 그 문단만 뺀다.
// 코드 경로 답은 본문 끝에 면책을 넣고(백엔드 테스트로 고정된 규칙), 화면도 응답의 disclaimer를 답 아래에 따로 보여 줘
// 같은 문구가 두 번 보였다(2026-11-18 리허설). 면책 자체는 답 아래 disclaimer-note로 한 번 남는다.
// 문구가 조금이라도 다르면(다른 경고·기준일 안내 등) 아무것도 빼지 않는다.
export function stripDuplicateDisclaimer(answer: string, disclaimer: string | null | undefined): string {
  const d = (disclaimer ?? '').trim()
  if (!d) return answer
  const body = answer.trimEnd()
  if (!body.endsWith(d)) return answer
  const head = body.slice(0, body.length - d.length)
  // 문단 경계(빈 줄·줄바꿈)나 본문 전체일 때만 뺀다. 문장 중간에 같은 글자가 이어 붙은 경우는 건드리지 않는다.
  if (head !== '' && !/\n\s*$/.test(head)) return answer
  return head.trimEnd()
}
