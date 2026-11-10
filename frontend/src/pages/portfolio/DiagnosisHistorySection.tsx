import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { api } from '../../api'
import { queryKeys } from '../../api/queryKeys'
import DiagnosisCompareView from '../../components/DiagnosisCompareView'
import DiagnosisResultView from '../../components/DiagnosisResultView'
import Pagination from '../../components/Pagination'
import { useLocale } from '../../i18n/useLocale'
import { formatDate, formatPrice } from '../../utils/format'
import ErrorNotice from '../../components/ui/ErrorNotice'

const DIAGNOSIS_HISTORY_PAGE_SIZE = 10

export default function DiagnosisHistorySection() {
  const { t } = useLocale()
  const won = t('unit.won', { fallback: '원' })
  const queryClient = useQueryClient()
  const [offset, setOffset] = useState(0)
  const [expanded, setExpanded] = useState<{ id: number; view: 'detail' | 'compare' } | null>(null)

  const { data, error, isPending, refetch } = useQuery({
    queryKey: queryKeys.diagnosisHistory(DIAGNOSIS_HISTORY_PAGE_SIZE, offset),
    queryFn: () => api.getDiagnosisHistory(DIAGNOSIS_HISTORY_PAGE_SIZE, offset),
  })

  const detailQuery = useQuery({
    queryKey: queryKeys.diagnosisDetail(expanded?.id ?? -1),
    queryFn: () => api.getDiagnosisDetail(expanded!.id),
    enabled: expanded?.view === 'detail',
  })
  const compareQuery = useQuery({
    queryKey: queryKeys.diagnosisCompare(expanded?.id ?? -1),
    queryFn: () => api.compareDiagnosis(expanded!.id),
    enabled: expanded?.view === 'compare',
  })

  const deleteMutation = useMutation({
    mutationFn: (id: number) => api.deleteDiagnosis(id),
    onSuccess: () => {
      setExpanded(null)
      void queryClient.invalidateQueries({ queryKey: ['diagnosisHistory'] })
    },
  })

  function toggle(id: number, view: 'detail' | 'compare') {
    setExpanded((prev) => (prev?.id === id && prev.view === view ? null : { id, view }))
  }

  async function handleDelete(id: number) {
    if (!confirm(t('diagnosis.delete_confirm', { fallback: '이 진단을 삭제할까요? 되돌릴 수 없어요.' }))) return
    await deleteMutation.mutateAsync(id)
  }

  return (
    <section>
      <h2 className="section-title">{t('diagnosis.history_title', { fallback: '진단 이력' })}</h2>
      <div className="card">
        {isPending && <p className="muted">{t('common.loading')}</p>}
        {error && <ErrorNotice error={error} onRetry={() => void refetch()} />}
        {data && data.items.length === 0 && (
          <p className="muted">{t('diagnosis.history_empty', { fallback: '아직 저장된 진단이 없어요. 위에서 AI 진단을 받아보세요.' })}</p>
        )}

        {data && data.items.length > 0 && (
          <ul className="ledger">
            {data.items.map((item) => (
              <li key={item.id} className="ledger-row">
                <div className="ledger-summary diagnosis-history-row">
                  <div>
                    <div className="ledger-head">
                      <span className="num">{formatDate(item.created_at)}</span>
                      <span>
                        {item.source === 'llm'
                          ? t('diagnosis.source_llm', { fallback: 'AI 설명' })
                          : t('diagnosis.source_rule_based', { fallback: '규칙 기반' })}
                      </span>
                    </div>
                    <div className="meta muted small">
                      <span className="num">
                        {formatPrice(item.total_asset)}
                        {won}
                      </span>
                      <span>{t('diagnosis.holdings_count', { fallback: '{n}종목', vars: { n: item.holdings_count } })}</span>
                      {item.flags.length > 0 && (
                        <span>{t('diagnosis.flags_count', { fallback: '플래그 {n}개', vars: { n: item.flags.length } })}</span>
                      )}
                    </div>
                  </div>
                  <div className="watch-right diagnosis-history-actions">
                    <button type="button" className="diagnosis-history-toggle" onClick={() => toggle(item.id, 'detail')}>
                      {expanded?.id === item.id && expanded.view === 'detail'
                        ? t('diagnosis.detail_close', { fallback: '상세 닫기' })
                        : t('diagnosis.detail_open', { fallback: '상세' })}
                    </button>
                    <button type="button" className="diagnosis-history-toggle" onClick={() => toggle(item.id, 'compare')}>
                      {expanded?.id === item.id && expanded.view === 'compare'
                        ? t('diagnosis.compare_close', { fallback: '비교 닫기' })
                        : t('diagnosis.compare_previous', { fallback: '이전과 비교' })}
                    </button>
                    <button
                      type="button"
                      className="delete-report"
                      onClick={() => void handleDelete(item.id)}
                      disabled={deleteMutation.isPending}
                    >
                      {t('common.delete', { fallback: '삭제' })}
                    </button>
                  </div>
                </div>

                {expanded?.id === item.id && expanded.view === 'detail' && (
                  <div className="ledger-detail">
                    {detailQuery.isPending && <p className="muted">{t('common.loading')}</p>}
                    {detailQuery.error && <ErrorNotice error={detailQuery.error} onRetry={() => void detailQuery.refetch()} />}
                    {detailQuery.data && <DiagnosisResultView result={detailQuery.data} />}
                  </div>
                )}
                {expanded?.id === item.id && expanded.view === 'compare' && (
                  <div className="ledger-detail">
                    {compareQuery.isPending && <p className="muted">{t('diagnosis.comparing', { fallback: '비교하는 중…' })}</p>}
                    {compareQuery.error && <ErrorNotice error={compareQuery.error} onRetry={() => void compareQuery.refetch()} />}
                    {compareQuery.data && <DiagnosisCompareView compare={compareQuery.data} />}
                  </div>
                )}
              </li>
            ))}
          </ul>
        )}

        {data && <Pagination offset={offset} pageSize={DIAGNOSIS_HISTORY_PAGE_SIZE} total={data.total} onChange={setOffset} />}
      </div>
    </section>
  )
}
