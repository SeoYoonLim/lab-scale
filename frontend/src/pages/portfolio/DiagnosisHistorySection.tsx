import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'
import { api } from '../../api'
import { queryKeys } from '../../api/queryKeys'
import DiagnosisCompareView from '../../components/DiagnosisCompareView'
import DiagnosisResultView from '../../components/DiagnosisResultView'
import Pagination from '../../components/Pagination'
import { useLocale } from '../../i18n/useLocale'
import { formatDate, formatPrice } from '../../utils/format'
import ErrorNotice from '../../components/ui/ErrorNotice'
import LiveStatus from '../../components/ui/LiveStatus'
import LoadingNotice from '../../components/ui/LoadingNotice'

const DIAGNOSIS_HISTORY_PAGE_SIZE = 10

export default function DiagnosisHistorySection() {
  const { t } = useLocale()
  const won = t('unit.won', { fallback: '원' })
  const queryClient = useQueryClient()
  const [offset, setOffset] = useState(0)
  const [expanded, setExpanded] = useState<{ id: number; view: 'detail' | 'compare' } | null>(null)
  const [deleteError, setDeleteError] = useState<unknown>(null)
  const [announcement, setAnnouncement] = useState('')

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

  // 삭제한 진단의 위치. 눌렀던 삭제 버튼이 사라져 초점이 문서 맨 위로 날아가지 않도록, 목록이 바뀐 뒤 다음 줄의 삭제 버튼
  // (없으면 구역 제목)으로 옮긴다.
  const listRef = useRef<HTMLUListElement>(null)
  const headingRef = useRef<HTMLHeadingElement>(null)
  const focusAfterDelete = useRef<number | null>(null)
  useEffect(() => {
    const index = focusAfterDelete.current
    if (index == null) return
    focusAfterDelete.current = null
    const buttons = listRef.current?.querySelectorAll<HTMLButtonElement>('button.delete-report')
    if (buttons && buttons.length > 0) buttons[Math.max(0, Math.min(index, buttons.length - 1))].focus()
    else headingRef.current?.focus()
  }, [data])

  const deleteMutation = useMutation({
    mutationFn: (id: number) => api.deleteDiagnosis(id),
    onSuccess: () => {
      setExpanded(null)
      void queryClient.invalidateQueries({ queryKey: ['diagnosisHistory'] })
    },
  })

  const detailLabel = (id: number) =>
    expanded?.id === id && expanded.view === 'detail'
      ? t('diagnosis.detail_close', { fallback: '상세 닫기' })
      : t('diagnosis.detail_open', { fallback: '상세' })
  const compareLabel = (id: number) =>
    expanded?.id === id && expanded.view === 'compare'
      ? t('diagnosis.compare_close', { fallback: '비교 닫기' })
      : t('diagnosis.compare_previous', { fallback: '이전과 비교' })

  function toggle(id: number, view: 'detail' | 'compare') {
    setExpanded((prev) => (prev?.id === id && prev.view === view ? null : { id, view }))
  }

  async function handleDelete(id: number) {
    if (!confirm(t('diagnosis.delete_confirm', { fallback: '이 진단을 삭제할까요? 되돌릴 수 없어요.' }))) return
    focusAfterDelete.current = data?.items.findIndex((row) => row.id === id) ?? null
    setDeleteError(null)
    setAnnouncement('')
    try {
      await deleteMutation.mutateAsync(id)
      setAnnouncement(t('diagnosis.deleted_status', { fallback: '진단을 삭제했어요.' }))
    } catch (err) {
      focusAfterDelete.current = null
      // 예전에는 여기서 다시 던져서 아무도 받지 않았다(오류가 화면에 안 보였다) - 화면에 보여준다
      setDeleteError(err)
    }
  }

  return (
    <section>
      <h2 className="section-title" ref={headingRef} tabIndex={-1}>
        {t('diagnosis.history_title', { fallback: '진단 이력' })}
      </h2>
      <div className="card">
        <LiveStatus message={announcement} />
        {deleteError != null && <ErrorNotice error={deleteError} fallback={t('diagnosis.delete_failed', { fallback: '진단을 삭제하지 못했어요.' })} />}
        {isPending && <LoadingNotice />}
        {error && <ErrorNotice error={error} onRetry={() => void refetch()} />}
        {data && data.items.length === 0 && (
          <p className="muted">{t('diagnosis.history_empty', { fallback: '아직 저장된 진단이 없어요. 위에서 AI 진단을 받아보세요.' })}</p>
        )}

        {data && data.items.length > 0 && (
          <ul className="ledger" ref={listRef}>
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
                    {/* 줄마다 같은 이름("상세", "삭제")이면 어느 진단인지 알 수 없어서 날짜를 이름에 붙인다. 펼침 상태도 알린다. */}
                    <button
                      type="button"
                      className="diagnosis-history-toggle"
                      aria-expanded={expanded?.id === item.id && expanded.view === 'detail'}
                      aria-label={`${detailLabel(item.id)} (${formatDate(item.created_at)})`}
                      onClick={() => toggle(item.id, 'detail')}
                    >
                      {detailLabel(item.id)}
                    </button>
                    <button
                      type="button"
                      className="diagnosis-history-toggle"
                      aria-expanded={expanded?.id === item.id && expanded.view === 'compare'}
                      aria-label={`${compareLabel(item.id)} (${formatDate(item.created_at)})`}
                      onClick={() => toggle(item.id, 'compare')}
                    >
                      {compareLabel(item.id)}
                    </button>
                    <button
                      type="button"
                      className="delete-report"
                      aria-label={`${t('common.delete', { fallback: '삭제' })} (${formatDate(item.created_at)})`}
                      aria-disabled={deleteMutation.isPending || undefined}
                      onClick={() => {
                        if (!deleteMutation.isPending) void handleDelete(item.id)
                      }}
                    >
                      {t('common.delete', { fallback: '삭제' })}
                    </button>
                  </div>
                </div>

                {expanded?.id === item.id && expanded.view === 'detail' && (
                  <div className="ledger-detail">
                    {detailQuery.isPending && <LoadingNotice />}
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
