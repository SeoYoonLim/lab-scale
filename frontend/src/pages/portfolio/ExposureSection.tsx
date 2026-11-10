import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { api } from '../../api'
import { queryKeys } from '../../api/queryKeys'
import LookThroughPanels from '../../components/LookThroughPanels'
import Banner from '../../components/ui/Banner'
import EmptyState from '../../components/ui/EmptyState'
import ErrorNotice from '../../components/ui/ErrorNotice'
import { useLocale } from '../../i18n/useLocale'
import { formatTradingDate } from '../../utils/format'

const COMPANY_LIMIT = 10

// ETF 실질 노출(look-through). 직접 보유와 ETF 구성종목 비중을 기업별로 합친 노출이고, 비중의 분모는 총자산(현금 포함)이다.
// 새 사용자·보유 없음도 200(status: empty)이라 오류가 아니라 안내 상태로 보여준다(핸드오프 7절).
export default function ExposureSection() {
  const { t, locale } = useLocale()
  const { data, error, isPending, refetch } = useQuery({
    queryKey: queryKeys.exposure(COMPANY_LIMIT, locale),
    queryFn: () => api.getLookThroughExposure({ limit: COMPANY_LIMIT }),
  })

  return (
    <section aria-labelledby="exposure-heading">
      <h2 className="section-title" id="exposure-heading">
        {t('look_through.title')}
      </h2>
      <div className="card">
        {isPending && <p className="muted">{t('common.loading')}</p>}
        {error && <ErrorNotice error={error} onRetry={() => void refetch()} />}

        {data && (
          <>
            {/* 종가·ETF 구성 기준일이 늦으면 맨 위에 먼저 알린다(핸드오프 4절) */}
            {data.stale && data.stale_notice && (
              <Banner tone="warn" title={t('common.stale_title', { fallback: '데이터가 최신이 아니에요' })}>
                {data.stale_notice}
              </Banner>
            )}
            {data.etf_holdings_stale && (
              <Banner tone="warn">
                {t('look_through.stale_badge')}: {t('exposure.etf_stale_note', { fallback: 'ETF 구성종목 기준일이 오래됐어요. 현재 구성과 다를 수 있어요.' })}
              </Banner>
            )}

            <p className="muted small">
              {t('look_through.title')} · {t('common.data_as_of', { fallback: '기준일' })}{' '}
              <span className="num">{formatTradingDate(data.valuation_date)}</span>
            </p>

            {data.status !== 'ok' ? (
              <EmptyState
                message={data.message ?? ''}
                action={
                  data.status === 'empty' ? (
                    <Link to="/portfolio" className="watchlist-button">
                      {t('performance.empty_cta', { fallback: '모의투자 하러 가기' })}
                    </Link>
                  ) : undefined
                }
              />
            ) : (
              <LookThroughPanels body={data} />
            )}
            <p className="disclaimer-note muted small">{data.disclaimer}</p>
          </>
        )}
      </div>
    </section>
  )
}
