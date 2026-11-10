import type { Locale } from './contracts/common'

// mock 리서치 답변의 언어별 문구. 실제 백엔드는 한국어 답을 먼저 만든 뒤 숫자·날짜·종목명·제목을 보호한 채 번역하거나
// (translated), 코드가 그 언어로 바로 쓰고(localized), 검증에 실패하면 그 언어 안내 + 데이터 요약만 준다(fallback_summary).
// mock은 번역기가 없으니 같은 구조의 문장을 언어별로 미리 써 둔 걸 고른다(핸드오프 15-1절의 세 상태를 화면에서 볼 수 있게).

interface Text {
  price: string
  cause: string
  evidence: string
  priceLine: (name: string, changePct: number | null, volume: string) => string
  causeLine: string
  noTool: string
  genericLookup: (name: string) => string
  intro: string
  introFollowUp: (question: string) => string
  translatedNote: string
  fallbackNote: string
  fallbackHeading: string
}

const TEXT: Record<Locale, Text> = {
  'ko-KR': {
    price: '주가 동향',
    cause: '원인 분석',
    evidence: '뉴스·공시 근거',
    priceLine: (name, changePct, volume) => `${name}의 최근 등락률은 ${changePct}%, 거래량은 ${volume}주예요.`,
    causeLine:
      '실제 백엔드가 연결되면 llama3.1:8b가 주가·뉴스 조회 결과를 종합해 이 자리에 원인을 서술해요. 샘플 응답에서는 조회한 결과만 보여드려요.',
    noTool:
      '도구를 호출하지 않고 바로 답하는 경우예요. 종목명(삼성전자, SK하이닉스, NAVER 등)을 넣어서 질문하면 근거(sources)도 함께 볼 수 있어요.',
    genericLookup: (name) =>
      `${name}에 대해 조회했어요. 실제 백엔드가 연결되면 llama3.1:8b가 도구 결과를 바탕으로 만든 답변이 여기에 표시돼요.`,
    intro: '[샘플 응답]',
    introFollowUp: (q) => `[샘플 응답 · "${q}"에 이어서]`,
    translatedNote: '',
    fallbackNote: '',
    fallbackHeading: '',
  },
  'en-US': {
    price: 'Price trend',
    cause: 'Cause analysis',
    evidence: 'News and filings',
    priceLine: (name, changePct, volume) => `${name}: recent daily change ${changePct}%, volume ${volume} shares.`,
    causeLine:
      'With the real backend, the AI writes the cause analysis here from the price and news results. This sample shows the looked-up data only.',
    noTool:
      'This is an answer without tool calls. Include a stock name (e.g. 삼성전자, SK하이닉스, NAVER) to see sources as well.',
    genericLookup: (name) => `Looked up ${name}. With the real backend, the AI answer built from the tool results appears here.`,
    intro: '[Sample answer]',
    introFollowUp: (q) => `[Sample answer · following up on "${q}"]`,
    translatedNote: '※ Translated from the Korean answer. Numbers, dates, names and titles are kept as in the original.',
    fallbackNote: '※ The translation could not be verified, so only a summary of the data is shown.',
    fallbackHeading: 'Data summary',
  },
  'ja-JP': {
    price: '株価動向',
    cause: '原因分析',
    evidence: 'ニュース・開示の根拠',
    priceLine: (name, changePct, volume) => `${name}の直近の騰落率は${changePct}%、出来高は${volume}株です。`,
    causeLine:
      '実際のバックエンドでは、AIが株価とニュースの結果をもとにここへ原因を書きます。このサンプルでは調べた結果のみ表示します。',
    noTool: 'ツールを呼ばずに答えるケースです。銘柄名(삼성전자、SK하이닉스、NAVERなど)を入れて質問すると根拠も確認できます。',
    genericLookup: (name) => `${name}について調べました。実際のバックエンドでは、ツール結果をもとにしたAIの回答がここに表示されます。`,
    intro: '[サンプル回答]',
    introFollowUp: (q) => `[サンプル回答 · 「${q}」の続き]`,
    translatedNote: '※ 韓国語の回答を翻訳しました。数値・日付・銘柄名・タイトルは原文のままです。',
    fallbackNote: '※ 翻訳を検証できなかったため、データの要約のみ表示します。',
    fallbackHeading: 'データ要約',
  },
  'zh-CN': {
    price: '股价走势',
    cause: '原因分析',
    evidence: '新闻与公告依据',
    priceLine: (name, changePct, volume) => `${name}近期涨跌幅为${changePct}%，成交量为${volume}股。`,
    causeLine: '连接真实后端后，AI 会根据股价和新闻查询结果在此撰写原因分析。本示例仅显示查询到的数据。',
    noTool: '这是未调用工具直接回答的情况。输入股票名称(삼성전자、SK하이닉스、NAVER 等)可同时查看依据。',
    genericLookup: (name) => `已查询${name}。连接真实后端后，这里会显示基于工具结果的 AI 回答。`,
    intro: '[示例回答]',
    introFollowUp: (q) => `[示例回答 · 接续"${q}"]`,
    translatedNote: '※ 本回答译自韩语原文。数字、日期、名称和标题保持原文。',
    fallbackNote: '※ 未能验证翻译，因此仅显示数据摘要。',
    fallbackHeading: '数据摘要',
  },
}

export function researchText(locale: Locale): Text {
  return TEXT[locale]
}
