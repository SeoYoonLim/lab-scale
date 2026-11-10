// 리서치 답변을 아주 가볍게 파싱한다. 서버가 직접 조립하는 섹션(backend/app/agent.py의
// ANSWER_STRUCTURE_INSTRUCTION, app/etf.py, app/event_answer.py)은 이 모양을 그대로 따른다:
//   - 섹션 제목은 반드시 줄 맨 앞의 "## 제목"
//   - 섹션 안에서 "- 항목"은 글머리 목록, "  - 하위"/"  · 하위"는 그 항목의 하위 줄
//   - 그 밖의 줄은 그냥 문단
// 반면 모델이 자유롭게 쓰는 문장(원인 분석 등)에는 `- ` 대신 `*   `을 쓰는 예도 있어
// (backend/API.md의 POST /api/research 응답 예시), 글머리표는 "-"/"*" 둘 다로 인식한다.
// 제목이 하나도 없는 답변(개념 질문 등, backend 지시상 "섹션 없이 한두 문장")도 그대로 받아,
// title이 없는 섹션 하나로 돌려준다 - 호출하는 쪽에서 분기하지 않아도 되게.
export interface AnswerSection {
  title: string | null
  lines: string[]
}

const HEADING_RE = /^##\s+(.+?)\s*$/

export function splitAnswerSections(answer: string): AnswerSection[] {
  const rawLines = answer.replace(/\r\n/g, '\n').split('\n')
  const sections: AnswerSection[] = []
  let current: AnswerSection = { title: null, lines: [] }

  function flush() {
    if (current.title != null || current.lines.some((l) => l.trim() !== '')) {
      sections.push(current)
    }
  }

  for (const line of rawLines) {
    const match = HEADING_RE.exec(line)
    if (match) {
      flush()
      current = { title: match[1], lines: [] }
    } else {
      current.lines.push(line)
    }
  }
  flush()
  return sections
}

// 섹션 본문을 문단/목록 블록으로 나눈다(빈 줄로 구분). 블록의 첫 줄이 "- " 또는 "* "로 시작하면
// 목록, 아니면 문단이다. "  - "/"  * "/"  · "로 시작하는 줄은 바로 앞 목록 항목의 하위 항목이다.
export interface TextBlock {
  type: 'text'
  text: string
}
export interface ListBlock {
  type: 'list'
  items: { text: string; children: string[] }[]
}
export type AnswerBlock = TextBlock | ListBlock

const TOP_BULLET_RE = /^[-*]\s+(.*)$/
const SUB_BULLET_RE = /^\s+[-*·]\s?(.*)$/

export function toBlocks(lines: string[]): AnswerBlock[] {
  const blocks: AnswerBlock[] = []
  let group: string[] = []

  function flushGroup() {
    if (group.length === 0) return
    if (TOP_BULLET_RE.test(group[0])) {
      const items: ListBlock['items'] = []
      for (const line of group) {
        const top = TOP_BULLET_RE.exec(line)
        if (top) {
          items.push({ text: top[1], children: [] })
          continue
        }
        const sub = SUB_BULLET_RE.exec(line)
        if (sub && items.length > 0) {
          items[items.length - 1].children.push(sub[1])
          continue
        }
        // 글머리표 없이 이어지는 줄(줄바꿈만 된 긴 문장)은 바로 앞 항목에 붙인다.
        if (items.length > 0) items[items.length - 1].text += ` ${line.trim()}`
      }
      blocks.push({ type: 'list', items })
    } else {
      blocks.push({ type: 'text', text: group.join('\n') })
    }
    group = []
  }

  for (const line of lines) {
    if (line.trim() === '') {
      flushGroup()
    } else {
      group.push(line)
    }
  }
  flushGroup()
  return blocks
}
