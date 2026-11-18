import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import SourceFootnotes from './components/SourceFootnotes'
import { formatTime } from './utils/format'

// 실제 데이터 확인에서 나온 파트너 요청: 근거 자료가 없으면 근거 영역을 보여주지 않는다
describe('SourceFootnotes without sources', () => {
  it('근거 문서가 없으면 영역 전체를 숨긴다(도구 칩만 남기지 않는다)', () => {
    const { container } = render(<SourceFootnotes usedTools={['stock_tool', 'news_tool']} sources={[]} />)
    expect(container).toBeEmptyDOMElement()
  })

  it('근거 문서가 있으면 "주요 근거 자료"로 보인다', () => {
    render(
      <SourceFootnotes
        usedTools={['news_tool']}
        sources={[{ type: 'news', title: '삼성전자 기사', url: 'https://example.com/a' }] as never}
      />,
    )
    expect(screen.getByText('주요 근거 자료')).toBeInTheDocument()
    expect(screen.getByText('삼성전자 기사')).toBeInTheDocument()
  })
})

describe('formatTime', () => {
  it('실시간 시세의 기준 시각을 한국 시간 HH:MM으로 보여준다', () => {
    expect(formatTime('2026-11-18T11:22:04.923306+09:00')).toMatch(/11[:.]22/)
  })
})
