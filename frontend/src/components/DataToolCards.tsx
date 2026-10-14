import type { ReactNode } from 'react'
import { IconChart, IconFile, IconGlobe, IconNews, IconSearch } from './icons'

// backend/API.md의 research 도구(used_tools)와 같은 구성. 어떤 데이터를 근거로 쓰는지 미리 보여준다.
const TOOLS: { title: string; desc: string; icon: ReactNode }[] = [
  { title: '주가 데이터', desc: '종가·등락률·거래량', icon: <IconChart /> },
  { title: '뉴스 데이터', desc: '종목 관련 주요 기사', icon: <IconNews /> },
  { title: '공시 데이터', desc: '전자공시(DART) 정보', icon: <IconFile /> },
  { title: '시장 지수', desc: '코스피·코스닥 비교', icon: <IconGlobe /> },
  { title: '의미 검색', desc: '뉴스·공시 본문 검색', icon: <IconSearch /> },
]

export default function DataToolCards() {
  return (
    <section className="data-tools" aria-label="분석에 사용되는 데이터">
      <h2 className="data-tools-title">분석에 사용되는 데이터 및 도구</h2>
      <ul>
        {TOOLS.map((tool) => (
          <li key={tool.title}>
            <span className="data-tool-icon">{tool.icon}</span>
            <span className="data-tool-text">
              <strong>{tool.title}</strong>
              <span className="muted small">{tool.desc}</span>
            </span>
          </li>
        ))}
      </ul>
    </section>
  )
}
