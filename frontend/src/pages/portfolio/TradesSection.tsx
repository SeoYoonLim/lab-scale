import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import type { FormEvent } from 'react'
import { api } from '../../api'
import { queryKeys } from '../../api/queryKeys'
import AssetTypeBadge from '../../components/AssetTypeBadge'
import Pagination from '../../components/Pagination'
import { useLocale } from '../../i18n/useLocale'
import { formatDate, formatPrice } from '../../utils/format'
import ErrorNotice from '../../components/ui/ErrorNotice'

const TRADES_PAGE_SIZE = 20

export default function TradesSection() {
  const { t } = useLocale()
  const won = t('unit.won', { fallback: '원' })
  const [offset, setOffset] = useState(0)
  const [tickerFilter, setTickerFilter] = useState('')
  const [appliedFilter, setAppliedFilter] = useState('')
  // placeOrder/resetPortfolio 뮤테이션이 ['trades']를 무효화하면(쿼리 키 접두어 일치) 이 쿼리도
  // 자동으로 다시 불러온다 - reloadKey를 prop으로 내려받지 않아도 된다.
  const { data, error, isPending, refetch } = useQuery({
    queryKey: queryKeys.trades(TRADES_PAGE_SIZE, offset, appliedFilter || undefined),
    queryFn: () => api.getTrades(TRADES_PAGE_SIZE, offset, appliedFilter || undefined),
    placeholderData: keepPreviousData,
  })

  function handleFilterSubmit(e: FormEvent) {
    e.preventDefault()
    setOffset(0)
    setAppliedFilter(tickerFilter.trim())
  }

  return (
    <section>
      <h2 className="section-title">{t('portfolio.tabs.trades', { fallback: '체결 내역' })}</h2>
      <div className="card">
        <form className="inline-form" onSubmit={handleFilterSubmit}>
          <input
            value={tickerFilter}
            onChange={(e) => setTickerFilter(e.target.value)}
            placeholder={t('trades.filter_placeholder', { fallback: '종목명으로 필터 (예: 삼성전자)' })}
            aria-label={t('trades.filter_aria', { fallback: '체결 내역 종목 필터' })}
          />
          <button type="submit">{t('trades.filter', { fallback: '필터' })}</button>
        </form>

        {isPending && <p className="muted">{t('common.loading')}</p>}
        {error && <ErrorNotice error={error} onRetry={() => void refetch()} />}
        {data && data.items.length === 0 && <p className="muted">{t('trades.empty', { fallback: '체결 내역이 없어요.' })}</p>}

        {data && data.items.length > 0 && (
          <ul className="ledger">
            {data.items.map((trade) => (
              <li key={trade.id} className="ledger-row watch-row">
                <div>
                  <div className="ledger-question">
                    {trade.company_name} <AssetTypeBadge assetType={trade.asset_type} />
                  </div>
                  <div className="meta muted small">
                    <span>
                      {trade.side === 'buy'
                        ? t('holdings.buy', { fallback: '매수' })
                        : t('holdings.sell', { fallback: '매도' })}
                    </span>
                    <span>{t('holdings.qty_shares', { fallback: '{n}주', vars: { n: trade.quantity } })}</span>
                    <span>
                      {formatPrice(trade.price)}
                      {won}
                    </span>
                    <span>{formatDate(trade.executed_at)}</span>
                  </div>
                </div>
                <div className="watch-right">
                  <span className="num">
                    {formatPrice(trade.amount)}
                    {won}
                  </span>
                </div>
              </li>
            ))}
          </ul>
        )}

        {data && <Pagination offset={offset} pageSize={TRADES_PAGE_SIZE} total={data.total} onChange={setOffset} />}
      </div>
    </section>
  )
}
