import type { ReactNode } from 'react'
import { useLocale } from '../i18n/useLocale'
import { IconChart, IconFile, IconGlobe, IconNews, IconSearch } from './icons'

interface Label {
  key: string
  fallback: string
}

// backend/API.md의 research 도구(used_tools)와 같은 구성. 어떤 데이터를 근거로 쓰는지 미리 보여준다.
const TOOLS: { title: Label; desc: Label; icon: ReactNode }[] = [
  {
    title: { key: 'datatools.price_title', fallback: '주가 데이터' },
    desc: { key: 'datatools.price_desc', fallback: '종가·등락률·거래량' },
    icon: <IconChart />,
  },
  {
    title: { key: 'datatools.news_title', fallback: '뉴스 데이터' },
    desc: { key: 'datatools.news_desc', fallback: '종목 관련 주요 기사' },
    icon: <IconNews />,
  },
  {
    title: { key: 'datatools.disclosure_title', fallback: '공시 데이터' },
    desc: { key: 'datatools.disclosure_desc', fallback: '전자공시(DART) 정보' },
    icon: <IconFile />,
  },
  {
    title: { key: 'datatools.index_title', fallback: '시장 지수' },
    desc: { key: 'datatools.index_desc', fallback: '코스피·코스닥 비교' },
    icon: <IconGlobe />,
  },
  {
    title: { key: 'datatools.semantic_title', fallback: '의미 검색' },
    desc: { key: 'datatools.semantic_desc', fallback: '뉴스·공시 본문 검색' },
    icon: <IconSearch />,
  },
]

export default function DataToolCards() {
  const { t } = useLocale()
  return (
    <section className="data-tools" aria-label={t('datatools.aria', { fallback: '분석에 사용되는 데이터' })}>
      <h2 className="data-tools-title">{t('datatools.section_title', { fallback: '분석에 사용되는 데이터 및 도구' })}</h2>
      <ul>
        {TOOLS.map((tool) => (
          <li key={tool.title.key}>
            <span className="data-tool-icon">{tool.icon}</span>
            <span className="data-tool-text">
              <strong>{t(tool.title.key, { fallback: tool.title.fallback })}</strong>
              <span className="muted small">{t(tool.desc.key, { fallback: tool.desc.fallback })}</span>
            </span>
          </li>
        ))}
      </ul>
    </section>
  )
}
