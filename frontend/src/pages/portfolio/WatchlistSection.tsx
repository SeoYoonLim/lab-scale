import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import type { FormEvent } from 'react'
import { api } from '../../api'
import { ApiError } from '../../api/errors'
import { queryKeys } from '../../api/queryKeys'
import AssetTypeBadge from '../../components/AssetTypeBadge'
import type { WatchlistItem } from '../../types'
import { formatDate, formatPct, formatPrice, trendClass } from '../../utils/format'

export default function WatchlistSection() {
  const queryClient = useQueryClient()
  const { data, error, isPending } = useQuery({ queryKey: queryKeys.watchlist, queryFn: () => api.listWatchlist() })
  const [ticker, setTicker] = useState('')
  const [formError, setFormError] = useState<string | null>(null)
  const [formNotice, setFormNotice] = useState<string | null>(null)

  const addMutation = useMutation({
    mutationFn: (t: string) => api.addWatchlistItem(t),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: queryKeys.watchlist }),
  })
  const removeMutation = useMutation({
    mutationFn: (t: string) => api.removeWatchlistItem(t),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: queryKeys.watchlist }),
  })

  async function handleAdd(e: FormEvent) {
    e.preventDefault()
    if (!ticker.trim() || addMutation.isPending) return
    setFormError(null)
    setFormNotice(null)
    try {
      await addMutation.mutateAsync(ticker.trim())
      setTicker('')
    } catch (err) {
      // CompanyPage의 "이미 관심종목이에요"와 똑같이, 이미 등록된 종목(409)은 에러가 아니라
      // 안내로 보여준다 - 메시지 자체는 서버/mock이 주는 문구를 그대로 쓴다.
      if (err instanceof ApiError && err.status === 409) {
        setFormNotice(err.message)
      } else {
        setFormError(err instanceof Error ? err.message : '추가하지 못했어요.')
      }
    }
  }

  async function handleRemove(item: WatchlistItem) {
    try {
      await removeMutation.mutateAsync(item.ticker)
    } catch (err) {
      setFormError(err instanceof Error ? err.message : '삭제하지 못했어요.')
    }
  }

  return (
    <section>
      <h2 className="section-title">관심종목</h2>
      <div className="card">
        <form className="inline-form" onSubmit={handleAdd}>
          <input
            value={ticker}
            onChange={(e) => setTicker(e.target.value)}
            placeholder="종목명 또는 종목코드 (예: 삼성전자)"
            aria-label="관심종목에 추가할 종목"
          />
          <button type="submit" disabled={addMutation.isPending || !ticker.trim()}>
            추가
          </button>
        </form>
        {formError && <p className="notice notice-error">{formError}</p>}
        {formNotice && <p className="muted small">{formNotice}</p>}

        {isPending && <p className="muted">불러오는 중…</p>}
        {error && <p className="notice notice-error">{error.message}</p>}
        {data && data.length === 0 && <p className="muted">아직 등록한 관심종목이 없어요.</p>}

        <ul className="ledger">
          {data?.map((item) => (
            <li key={item.ticker} className="ledger-row watch-row">
              <div>
                <div className="ledger-question">
                  {item.company_name} <AssetTypeBadge assetType={item.asset_type} />
                </div>
                <div className="meta muted small">
                  <span>{item.ticker}</span>
                  <span>{formatDate(item.added_at)} 등록</span>
                </div>
              </div>
              <div className="watch-right">
                {item.latest_close != null ? (
                  <div className="meta meta-end">
                    <span className="num">{formatPrice(item.latest_close)}원</span>
                    <span className={`num ${trendClass(item.change_pct)}`}>{formatPct(item.change_pct)}</span>
                  </div>
                ) : (
                  <span className="muted small">가격 없음</span>
                )}
                <button type="button" className="delete-report" onClick={() => void handleRemove(item)}>
                  삭제
                </button>
              </div>
            </li>
          ))}
        </ul>
      </div>
    </section>
  )
}
