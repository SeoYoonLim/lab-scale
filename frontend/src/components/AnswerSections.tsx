import { splitAnswerSections, toBlocks } from '../utils/markdownSections'
import type { AnswerBlock } from '../utils/markdownSections'

function Blocks({ blocks }: { blocks: AnswerBlock[] }) {
  return (
    <>
      {blocks.map((block, i) =>
        block.type === 'list' ? (
          <ul className="answer-list" key={i}>
            {block.items.map((item, j) => (
              <li key={j}>
                <span>{item.text}</span>
                {item.children.length > 0 && (
                  <ul className="answer-sublist">
                    {item.children.map((child, k) => (
                      <li key={k}>{child}</li>
                    ))}
                  </ul>
                )}
              </li>
            ))}
          </ul>
        ) : (
          <p className="answer-text" key={i}>
            {block.text}
          </p>
        ),
      )}
    </>
  )
}

// 리서치/리포트 답변을 렌더링한다. backend/app/agent.py가 답변을 "## 섹션 제목"으로 구조화하도록
// 유도하므로(주가 동향/원인 분석/뉴스·공시 근거/시장 상황/위험요인 - 실제로 호출한 tool이 뒷받침하는
// 섹션만), 그 구조를 살려 섹션별 카드로 보여준다. 섹션 제목이 하나도 없는 답변(개념 질문 등)은
// 예전처럼 그냥 문단으로 보여준다.
export default function AnswerSections({ answer }: { answer: string }) {
  const sections = splitAnswerSections(answer)
  const hasHeadings = sections.some((s) => s.title != null)

  if (!hasHeadings) {
    return (
      <div className="answer-plain">
        {sections.map((section, i) => (
          <Blocks key={i} blocks={toBlocks(section.lines)} />
        ))}
      </div>
    )
  }

  // 번호는 제목 있는 섹션에만 매긴다(인트로 블록은 번호 없음).
  const numbers = sections.reduce<{ list: (number | null)[]; count: number }>(
    (acc, section) => {
      const count = section.title == null ? acc.count : acc.count + 1
      return { list: [...acc.list, section.title == null ? null : count], count }
    },
    { list: [], count: 0 },
  ).list

  return (
    <div className="answer-sections">
      {sections.map((section, i) =>
        section.title == null ? (
          <div className="answer-intro" key={i}>
            <Blocks blocks={toBlocks(section.lines)} />
          </div>
        ) : (
          <section className="answer-section" key={i}>
            <h3 className="answer-section-title">
              <span className="answer-section-no">{numbers[i]}</span>
              {section.title}
            </h3>
            <Blocks blocks={toBlocks(section.lines)} />
          </section>
        ),
      )}
    </div>
  )
}
