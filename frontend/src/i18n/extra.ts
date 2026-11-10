import type { Locale } from '../api/contracts/common'

// 백엔드가 준 리소스(docs/i18n/*.json)에 없는 화면 라벨. 한국어는 코드 안에 fallback으로 적어 두므로(t(key, { fallback })) 여기에는
// 영어만 둔다. 일본어·중국어는 아직 없어서 영어로 보인다(한국어로 보이는 것보다 낫다) - 번역을 받으면 이 객체에 언어를 추가하면 된다.
// 키는 "영역.이름" 형식이고, 새 화면 라벨을 추가할 때 여기에도 영어를 같이 적는다.
export const EXTRA_MESSAGES: Partial<Record<Locale, Record<string, string>>> = {
  'en-US': {
    'nav.companies': 'Stocks',
    'nav.portfolio': 'Portfolio',
    'nav.market': 'Market',
    'nav.logout': 'Log out',
    'nav.login': 'Log in',
    'common.back': 'Back',
    'common.next': 'Next',
    'common.prev': 'Previous',
    'common.dash': '—',
    'common.calculation_unavailable': 'Not available',
    'common.delayed_badge': 'Delayed',
    'common.original_korean': 'Korean original',
    'common.data_as_of': 'Data as of',
    'common.error_title': 'Something went wrong',
    'common.stale_title': 'Data may be out of date',

    'portfolio.title': 'Portfolio',
    'portfolio.tabs.holdings': 'Holdings & orders',
    'portfolio.tabs.performance': 'Performance',
    'portfolio.tabs.exposure': 'Look-through',
    'portfolio.tabs.diagnosis': 'AI diagnosis',
    'portfolio.tabs.trades': 'Trades',
    'portfolio.tabs.watchlist': 'Watchlist',

    'performance.period_label': 'Period',
    'performance.benchmark_label': 'Compare with',
    'performance.chart_title': 'Portfolio vs. index (start = 100)',
    'performance.empty_cta': 'Go to paper trading',
    'performance.partial_note': 'Some days are estimated.',
    'performance.reference_only': 'For reference',
    'performance.metrics_title': 'Risk and range',
    'performance.warnings_title': 'Notes',
    'performance.before_inception': 'Calculated from the first fill',

    'exposure.title': 'ETF look-through exposure',
    'exposure.bar_title': 'Share of total assets',
    'exposure.companies': 'Companies',
    'exposure.cash': 'Cash',
    'exposure.overlaps': 'Companies held directly and through ETFs',
    'exposure.etf_cards': 'ETF breakdown',
    'exposure.flags': 'Concentration alerts',
    'exposure.direct_top1': 'Largest direct holding',
    'exposure.look_through_top1': 'Largest look-through exposure',
    'exposure.type': 'Type',
    'exposure.show_via': 'Show ETFs',
    'exposure.hide_via': 'Hide ETFs',
    'exposure.no_companies': 'No ETF could be broken down by company. Only unidentified exposure is shown.',

    'briefing.title': "Today's briefing",
    'briefing.tab_today': 'Today',
    'briefing.tab_history': 'Past briefings',
    'briefing.checklist': 'Things to check today',
    'briefing.regenerate_confirm': 'Create a new version of today\'s briefing?',
    'briefing.created_note': 'Created {date}. Prices after that are not reflected.',
    'briefing.no_use_llm': 'Quick (no AI summary)',

    'history.title': 'My records',
    'research.hide_original': 'Show translation',
    'research.fallback_summary_notice': 'The translation could not be verified, so only a data summary is shown. You can view the Korean original.',

    'settings.language_saved_note': 'Screen labels and new answers use this language.',
    'settings.timezone_placeholder': 'e.g. Asia/Seoul',
  },
}
