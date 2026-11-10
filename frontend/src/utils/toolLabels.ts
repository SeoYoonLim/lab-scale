import type { LocaleContextValue } from '../i18n/localeContext'

// 백엔드 answer의 used_tools 이름을 사람이 읽는 라벨로. 라벨은 언어 설정을 따르고, 모르는 도구는 이름 그대로 보여준다.
const TOOL_LABEL_KEYS: Record<string, { key: string; fallback: string }> = {
  stock_tool: { key: 'tool.stock_tool', fallback: '주가 조회' },
  news_tool: { key: 'tool.news_tool', fallback: '뉴스 조회' },
  disclosure_tool: { key: 'tool.disclosure_tool', fallback: '공시 조회' },
  rag_search_tool: { key: 'tool.rag_search_tool', fallback: '의미 검색' },
  market_tool: { key: 'tool.market_tool', fallback: '시장 지수 비교' },
}

export function toolLabel(t: LocaleContextValue['t'], tool: string): string {
  const ref = TOOL_LABEL_KEYS[tool]
  return ref ? t(ref.key, { fallback: ref.fallback }) : tool
}
