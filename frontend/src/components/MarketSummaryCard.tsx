import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { api } from '../api'
import { freshnessTone } from '../api/contracts/briefing'
import { breadthBarSegments, changeUnavailableKey, indexLabelKey } from '../api/contracts/market'
import type { MarketIndexOverview } from '../api/contracts/market'
import { queryKeys } from '../api/queryKeys'
import { useLocale } from '../i18n/useLocale'
import { formatSignedPct, formatTradingDate, trendClass } from '../utils/format'
import Banner from './ui/Banner'
import EmptyState from './ui/EmptyState'
import ErrorNotice from './ui/ErrorNotice'

// 오늘의 시장(코스피·코스닥 종가와 상승·하락 종목 수). GET /api/market/overview는 공개 API라 로그인이나 오늘 브리핑 없이 읽힌다.
// 숫자는 서버 값을 그대로 보여주고, 등락이 null이면 0%가 아니라 "—"와 이유를 보여준다(핸드오프 41차).
export default function MarketSummaryCard() {
  const { t } = useLocale()
  const { data, error, isPending, refetch } = useQuery({
    queryKey: queryKeys.marketOverview,
    queryFn: () => api.getMarketOverview(),
  })

  const lag = data?.freshness.lag_business_days
  const tone = data ? freshnessTone(data.freshness.status) : 'none'
  const breadthBar = data?.breadth ? breadthBarSegments(data.breadth) : null

  return (
    <section className="card market-summary" aria-labelledby="market-summary-heading">
      <div className="card-head">
        <h2 id="market-summary-heading" className="section-title">
          {t('briefing.market_title', { fallback: '오늘의 시장' })}
        </h2>
        <Link to="/briefing" className="link-button">
          {t('nav.briefing')}
        </Link>
      </div>

      {isPending && <p className="muted small">{t('common.loading')}</p>}
      {error && <ErrorNotice error={error} onRetry={() => void refetch()} />}

      {data?.status === 'no_data' && (
        <EmptyState message={t('market.no_data', { fallback: '시장 데이터가 아직 없어요.' })} />
      )}

      {data && data.status !== 'no_data' && (
        <>
          {tone !== 'none' && (
            <Banner tone={tone === 'danger' ? 'danger' : 'warn'}>
              {t('market.delayed', {
                fallback: '종가가 {lag}거래일 늦었어요. 마지막 종가({date}) 기준이에요.',
                vars: { lag: lag ?? '—', date: formatTradingDate(data.as_of) },
              })}
            </Banner>
          )}
          {data.freshness.intraday_excluded && (
            <p className="muted small">
              {t('market.intraday_excluded', { fallback: '장 마감 전이라 오늘 시세는 쓰지 않고 직전 거래일 종가를 보여줘요.' })}
            </p>
          )}

          <div className="stat-grid">
            {data.indices.map((index) => (
              <IndexCard key={index.code} index={index} />
            ))}
          </div>

          {data.breadth && (
            <div className="market-breadth">
              <p className="muted small">
                {t('market.breadth_title', { fallback: '상승·하락 종목' })}{' '}
                <span className="num">{formatTradingDate(data.breadth.date)}</span>
              </p>
              {breadthBar ? (
                <>
                  <div className="breadth-bar" role="img" aria-label={t('market.breadth_title', { fallback: '상승·하락 종목' })}>
                    {breadthBar.map((seg) => (
                      <span key={seg.key} className={`breadth-seg breadth-seg-${seg.key}`} style={{ width: `${seg.pct}%` }} />
                    ))}
                  </div>
                  <ul className="breadth-legend">
                    <li>
                      <span className="up">{t('market.up', { fallback: '상승' })}</span>{' '}
                      <span className="num">{data.breadth.up}</span>
                    </li>
                    <li>
                      <span className="flat">{t('market.flat', { fallback: '보합' })}</span>{' '}
                      <span className="num">{data.breadth.flat}</span>
                    </li>
                    <li>
                      <span className="down">{t('market.down', { fallback: '하락' })}</span>{' '}
                      <span className="num">{data.breadth.down}</span>
                    </li>
                  </ul>
                </>
              ) : (
                <p className="muted small">{t('market.breadth_unavailable', { fallback: '상승·하락 비율을 계산할 수 없어요.' })}</p>
              )}
              {/* 시장 전체가 아니라 수집 대상 주식만 센 값이다 - 숫자 옆에 기준을 같이 적는다 */}
              <p className="muted small">
                {t('market.breadth_universe', {
                  fallback: '수집 대상 {n}종목 기준(시장 전체가 아니에요)',
                  vars: { n: data.breadth.total },
                })}
              </p>
            </div>
          )}

          <p className="muted small">
            {t('daily_briefing.data_as_of')} <span className="num">{formatTradingDate(data.as_of)}</span>
          </p>
        </>
      )}
    </section>
  )
}

function IndexCard({ index }: { index: MarketIndexOverview }) {
  const { t } = useLocale()
  const label = indexLabelKey(index.code)
  const reason = changeUnavailableKey(index.change_unavailable_reason)
  return (
    <div className="stat-card">
      <span className="muted small">{label ? t(label.key, { fallback: label.fallback }) : index.name}</span>
      <span className="stat-value num">{index.close != null ? index.close.toLocaleString('ko-KR') : '—'}</span>
      <span className={`num small ${trendClass(index.change_pct)}`}>{formatSignedPct(index.change_pct)}</span>
      {/* 등락을 못 구했을 때 0%로 그리지 않고 이유를 적는다 */}
      {index.change_pct == null && index.close != null && reason && (
        <span className="muted small">{t(reason.key, { fallback: reason.fallback })}</span>
      )}
    </div>
  )
}
