import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { api } from '../api'
import { freshnessTone } from '../api/contracts/briefing'
import { queryKeys } from '../api/queryKeys'
import { useLocale } from '../i18n/useLocale'
import { formatSignedPct, formatTradingDate, trendClass } from '../utils/format'
import Banner from './ui/Banner'
import ErrorNotice from './ui/ErrorNotice'

// 오늘의 시장(코스피·코스닥·원/달러). 지수·시장 요약 전용 REST는 없고 일일 브리핑의 market 영역에 들어 있어서, 오늘 브리핑이
// 있으면 거기서 읽어 보여준다(핸드오프: 시장 요약은 브리핑 응답). 없으면 만들러 가는 링크만 둔다 - 여기서 자동으로 만들지 않는다.
export default function MarketSummaryCard() {
  const { t, timezone } = useLocale()
  const { data, error, isPending, refetch } = useQuery({
    queryKey: queryKeys.dailyBriefingCurrent(timezone),
    queryFn: () => api.getCurrentDailyBriefing(timezone),
  })

  const market = data?.content.sections.market
  const freshness = data?.content.freshness

  return (
    <section className="card market-summary" aria-labelledby="market-summary-heading">
      <div className="card-head">
        <h2 id="market-summary-heading" className="section-title">
          {market?.title ?? t('briefing.market_title', { fallback: '오늘의 시장' })}
        </h2>
        <Link to="/briefing" className="link-button">
          {t('nav.briefing')}
        </Link>
      </div>

      {isPending && <p className="muted small">{t('common.loading')}</p>}
      {error && <ErrorNotice error={error} onRetry={() => void refetch()} />}
      {data === null && (
        <p className="muted small">
          {t('daily_briefing.not_generated')}{' '}
          <Link to="/briefing" className="link-button">
            {t('daily_briefing.generate')}
          </Link>
        </p>
      )}

      {freshness?.warning && (
        <Banner tone={freshnessTone(freshness.status) === 'danger' ? 'danger' : 'warn'}>{freshness.headline}</Banner>
      )}
      {market && (
        <>
          <div className="stat-grid">
            {market.indices.map((index) => (
              <div className="stat-card" key={index.code}>
                <span className="muted small">{index.name}</span>
                <span className="stat-value num">{index.close != null ? index.close.toLocaleString('ko-KR') : '—'}</span>
                <span className={`num small ${trendClass(index.change_pct)}`}>{formatSignedPct(index.change_pct)}</span>
              </div>
            ))}
          </div>
          <p className="muted small">
            {t('daily_briefing.data_as_of')} <span className="num">{formatTradingDate(data?.data_as_of)}</span>
          </p>
        </>
      )}
    </section>
  )
}
