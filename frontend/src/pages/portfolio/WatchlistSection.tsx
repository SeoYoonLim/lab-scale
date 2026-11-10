import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import type { FormEvent } from 'react'
import { api } from '../../api'
import { ApiError } from '../../api/errors'
import { queryKeys } from '../../api/queryKeys'
import AssetTypeBadge from '../../components/AssetTypeBadge'
import { useLocale } from '../../i18n/useLocale'
import type { WatchlistItem } from '../../types'
import { formatDate, formatPct, formatPrice, trendClass } from '../../utils/format'
import ErrorNotice from '../../components/ui/ErrorNotice'

export default function WatchlistSection() {
  const { t } = useLocale()
  const queryClient = useQueryClient()
  const { data, error, isPending, refetch } = useQuery({ queryKey: queryKeys.watchlist, queryFn: () => api.listWatchlist() })
  const [ticker, setTicker] = useState('')
  const [formError, setFormError] = useState<string | null>(null)
  const [formNotice, setFormNotice] = useState<string | null>(null)

  const addMutation = useMutation({
    mutationFn: (name: string) => api.addWatchlistItem(name),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: queryKeys.watchlist }),
  })
  const removeMutation = useMutation({
    mutationFn: (name: string) => api.removeWatchlistItem(name),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: queryKeys.watchlist }),
  })

  async function handleAdd(e: FormEvent) {
    e.preventDefault()
    if (!ticker.trim() || addMutation.isPending) return
    setFormError(null)
    setFormNotice(null)
    try {
      const input = ticker.trim()
      const added = await addMutation.mutateAsync(input)
      // 관심종목 추가는 유사 이름을 받아 가장 가까운 종목으로 등록한다(음차 "엘지전자" -> LG전자). 응답에는 "보정됨" 표시가 따로
      // 없어서, 입력과 등록된 이름·코드가 둘 다 다르면 실제로 어떤 종목이 등록됐는지 알려준다.
      if (input.toLowerCase() !== added.company_name.toLowerCase() && input !== added.ticker) {
        setFormNotice(
          t('watchlist.registered_as', {
            fallback: "'{input}' 대신 {name}({ticker})로 등록했어요.",
            vars: { input, name: added.company_name, ticker: added.ticker },
          }),
        )
      }
      setTicker('')
    } catch (err) {
      // CompanyPage의 "이미 관심종목이에요"와 똑같이, 이미 등록된 종목(409)은 에러가 아니라
      // 안내로 보여준다 - 메시지 자체는 서버/mock이 주는 문구를 그대로 쓴다.
      if (err instanceof ApiError && err.status === 409) {
        setFormNotice(err.message)
      } else {
        setFormError(err instanceof Error ? err.message : t('watchlist.add_failed', { fallback: '추가하지 못했어요.' }))
      }
    }
  }

  async function handleRemove(item: WatchlistItem) {
    try {
      await removeMutation.mutateAsync(item.ticker)
    } catch (err) {
      setFormError(err instanceof Error ? err.message : t('watchlist.remove_failed', { fallback: '삭제하지 못했어요.' }))
    }
  }

  return (
    <section>
      <h2 className="section-title">{t('portfolio.tabs.watchlist', { fallback: '관심종목' })}</h2>
      <div className="card">
        <form className="inline-form" onSubmit={handleAdd}>
          <input
            value={ticker}
            onChange={(e) => setTicker(e.target.value)}
            placeholder={t('watchlist.placeholder', { fallback: '종목명 또는 종목코드 (예: 삼성전자)' })}
            aria-label={t('watchlist.aria', { fallback: '관심종목에 추가할 종목' })}
          />
          <button type="submit" disabled={addMutation.isPending || !ticker.trim()}>
            {t('watchlist.add', { fallback: '추가' })}
          </button>
        </form>
        {formError && <p className="notice notice-error">{formError}</p>}
        {formNotice && <p className="muted small">{formNotice}</p>}

        {isPending && <p className="muted">{t('common.loading')}</p>}
        {error && <ErrorNotice error={error} onRetry={() => void refetch()} />}
        {data && data.length === 0 && <p className="muted">{t('watchlist.empty', { fallback: '아직 등록한 관심종목이 없어요.' })}</p>}

        <ul className="ledger">
          {data?.map((item) => (
            <li key={item.ticker} className="ledger-row watch-row">
              <div>
                <div className="ledger-question">
                  {item.company_name} <AssetTypeBadge assetType={item.asset_type} />
                </div>
                <div className="meta muted small">
                  <span>{item.ticker}</span>
                  <span>{t('watchlist.added_on', { fallback: '{date} 등록', vars: { date: formatDate(item.added_at) } })}</span>
                </div>
              </div>
              <div className="watch-right">
                {item.latest_close != null ? (
                  <div className="meta meta-end">
                    <span className="num">
                      {formatPrice(item.latest_close)}
                      {t('unit.won', { fallback: '원' })}
                    </span>
                    <span className={`num ${trendClass(item.change_pct)}`}>{formatPct(item.change_pct)}</span>
                  </div>
                ) : (
                  <span className="muted small">{t('watchlist.no_price', { fallback: '가격 없음' })}</span>
                )}
                <button type="button" className="delete-report" onClick={() => void handleRemove(item)}>
                  {t('watchlist.remove', { fallback: '삭제' })}
                </button>
              </div>
            </li>
          ))}
        </ul>
      </div>
    </section>
  )
}
