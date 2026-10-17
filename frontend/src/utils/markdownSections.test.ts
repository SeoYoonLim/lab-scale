import { describe, expect, it } from 'vitest'
import { splitAnswerSections, toBlocks } from './markdownSections'

describe('splitAnswerSections', () => {
  it('헤더가 없으면 title: null 섹션 하나로 돌려준다', () => {
    const sections = splitAnswerSections('공모주 청약이란 신주를 배정받는 것을 말해요.')
    expect(sections).toEqual([{ title: null, lines: ['공모주 청약이란 신주를 배정받는 것을 말해요.'] }])
  })

  it('"## 제목" 줄마다 새 섹션을 시작한다(backend/app/agent.py 형식)', () => {
    const answer = '## 주가 동향\n삼성전자는 20거래일간 5.2% 올랐어요.\n\n## 위험요인\n변동성이 큽니다.'
    const sections = splitAnswerSections(answer)
    expect(sections).toEqual([
      { title: '주가 동향', lines: ['삼성전자는 20거래일간 5.2% 올랐어요.', ''] },
      { title: '위험요인', lines: ['변동성이 큽니다.'] },
    ])
  })

  it('첫 "##" 앞의 문장은 title: null 인트로 섹션으로 남는다', () => {
    const answer = '[샘플 응답]\n\n## 주가 동향\n내용'
    const sections = splitAnswerSections(answer)
    expect(sections[0]).toEqual({ title: null, lines: ['[샘플 응답]', ''] })
    expect(sections[1].title).toBe('주가 동향')
  })

  it('빈 답변(공백뿐인 섹션)은 버린다', () => {
    expect(splitAnswerSections('   \n  ')).toEqual([])
  })
})

describe('toBlocks', () => {
  it('"- " 로 시작하는 블록은 목록으로, 아니면 문단으로 나눈다', () => {
    const blocks = toBlocks(['문단입니다.', '', '- 첫 항목', '- 둘째 항목'])
    expect(blocks).toEqual([
      { type: 'text', text: '문단입니다.' },
      {
        type: 'list',
        items: [
          { text: '첫 항목', children: [] },
          { text: '둘째 항목', children: [] },
        ],
      },
    ])
  })

  it('"*   " 글머리표도 목록으로 인식한다(backend/API.md 응답 예시에 쓰인 형식)', () => {
    const blocks = toBlocks(['현대차의 최근 뉴스는 다음과 같습니다.', '', '*   아반떼 가격 논란', '*   투싼 판매 호조'])
    expect(blocks).toEqual([
      { type: 'text', text: '현대차의 최근 뉴스는 다음과 같습니다.' },
      {
        type: 'list',
        items: [
          { text: '아반떼 가격 논란', children: [] },
          { text: '투싼 판매 호조', children: [] },
        ],
      },
    ])
  })

  it('"  - "/"  · " 하위 줄은 바로 앞 항목의 children으로 들어간다(backend/app/event_answer.py 형식)', () => {
    const blocks = toBlocks([
      '- 2026.10.12 · 뉴스 4건',
      '  · 삼성전자 분기보고서',
      '  - 기준 종가 74,700원(2026-10-11), 반응 시작일 2026-10-12',
    ])
    expect(blocks).toEqual([
      {
        type: 'list',
        items: [
          {
            text: '2026.10.12 · 뉴스 4건',
            children: ['삼성전자 분기보고서', '기준 종가 74,700원(2026-10-11), 반응 시작일 2026-10-12'],
          },
        ],
      },
    ])
  })
})
