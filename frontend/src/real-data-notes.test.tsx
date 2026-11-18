import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import SourceFootnotes from './components/SourceFootnotes'
import { formatTime } from './utils/format'

// 실제 데이터 확인에서 나온 파트너 요청: 근거 자료가 없을 때 "근거 자료"라고 부르지 않는다
describe('SourceFootnotes without sources', () => {
  it('근거 문서가 없으면 제목이 "사용한 분석 도구"이고 번호 각주는 없다', () => {
    render(<SourceFootnotes usedTools={['stock_tool', 'news_tool']} sources={[]} />)
    expect(screen.getByText('사용한 분석 도구')).toBeInTheDocument()
    expect(screen.queryByText('주요 근거 자료')).toBeNull()
    expect(screen.queryByRole('list', { name: /./ })).toBeNull()
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
