import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import type { FormEvent } from 'react'
import { api } from '../../api'
import { queryKeys } from '../../api/queryKeys'
import AssetTypeBadge from '../../components/AssetTypeBadge'
import Pagination from '../../components/Pagination'
import { formatDate, formatPrice } from '../../utils/format'

const TRADES_PAGE_SIZE = 20

export default function TradesSection() {
  const [offset, setOffset] = useState(0)
  const [tickerFilter, setTickerFilter] = useState('')
  const [appliedFilter, setAppliedFilter] = useState('')
  // placeOrder/resetPortfolio 뮤테이션이 ['trades']를 무효화하면(쿼리 키 접두어 일치) 이 쿼리도
  // 자동으로 다시 불러온다 - reloadKey를 prop으로 내려받지 않아도 된다.
  const { data, error, isPending } = useQuery({
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
      <h2 className="section-title">체결 내역</h2>
      <div className="card">
        <form className="inline-form" onSubmit={handleFilterSubmit}>
          <input
            value={tickerFilter}
            onChange={(e) => setTickerFilter(e.target.value)}
            placeholder="종목명으로 필터 (예: 삼성전자)"
            aria-label="체결 내역 종목 필터"
          />
          <button type="submit">필터</button>
        </form>

        {isPending && <p className="muted">불러오는 중…</p>}
        {error && <p className="notice notice-error">{error.message}</p>}
        {data && data.items.length === 0 && <p className="muted">체결 내역이 없어요.</p>}

        {data && data.items.length > 0 && (
          <ul className="ledger">
            {data.items.map((t) => (
              <li key={t.id} className="ledger-row watch-row">
                <div>
                  <div className="ledger-question">
                    {t.company_name} <AssetTypeBadge assetType={t.asset_type} />
                  </div>
                  <div className="meta muted small">
                    <span>{t.side === 'buy' ? '매수' : '매도'}</span>
                    <span>{t.quantity}주</span>
                    <span>{formatPrice(t.price)}원</span>
                    <span>{formatDate(t.executed_at)}</span>
                  </div>
                </div>
                <div className="watch-right">
                  <span className="num">{formatPrice(t.amount)}원</span>
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
