import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { api } from '../../api'
import { queryKeys } from '../../api/queryKeys'
import DiagnosisCompareView from '../../components/DiagnosisCompareView'
import DiagnosisResultView from '../../components/DiagnosisResultView'
import ErrorNotice from '../../components/ui/ErrorNotice'
import LiveStatus from '../../components/ui/LiveStatus'
import { useLocale } from '../../i18n/useLocale'

// 진단은 "항상 새로 계산"이고 결과를 캐시해서 재사용할 이유가 없다(backend/API.md) - 쿼리가 아니라
// 뮤테이션(버튼을 누르는 행동)으로 다룬다. 보유 종목이 바뀌면 부모가 key를 바꿔 이 컴포넌트를
// 통째로 리마운트시켜서 지난 결과를 지운다.
export default function DiagnosisSection() {
  const { t } = useLocale()
  const queryClient = useQueryClient()
  const [showCompare, setShowCompare] = useState(false)
  const diagnosisMutation = useMutation({
    mutationFn: () => api.getDiagnosis(),
    onSuccess: () => {
      setShowCompare(false)
      // 방금 받은 진단이 이력에 쌓였으니, 아래 이력 섹션도 다음에 보면 최신 상태이게 한다.
      void queryClient.invalidateQueries({ queryKey: ['diagnosisHistory'] })
    },
  })
  const result = diagnosisMutation.data
  const diagnosisId = result?.diagnosis_id ?? undefined

  const compareQuery = useQuery({
    queryKey: queryKeys.diagnosisCompare(diagnosisId ?? -1),
    queryFn: () => api.compareDiagnosis(diagnosisId!),
    enabled: showCompare && diagnosisId != null,
  })

  return (
    <section>
      <h2 className="section-title">{t('diagnosis.section_title', { fallback: 'AI 포트폴리오 진단' })}</h2>
      <div className="card">
        <div className="card-head">
          <p className="muted">{t('diagnosis.intro', { fallback: '보유 종목을 바탕으로 비중·손익·위험 요인을 진단해드려요.' })}</p>
          {/* disabled로 끄면 누른 버튼의 키보드 초점이 사라지므로 aria-disabled로 두고, 진행 중에는 눌러도 무시한다 */}
          <button
            type="button"
            className="send"
            onClick={() => {
              if (!diagnosisMutation.isPending) diagnosisMutation.mutate()
            }}
            aria-disabled={diagnosisMutation.isPending || undefined}
          >
            {diagnosisMutation.isPending ? t('diagnosis.running', { fallback: '진단 중…' }) : t('diagnosis.run_button', { fallback: 'AI 진단 받기' })}
          </button>
        </div>
        {/* 1분 넘게 걸릴 수 있는 작업이라, 시작과 끝을 소리로도 알린다 */}
        <LiveStatus
          message={
            diagnosisMutation.isPending
              ? t('diagnosis.running_note', { fallback: '보유 종목을 분석하고 있어요. 1분 정도 걸릴 수 있어요.' })
              : diagnosisMutation.isSuccess
                ? t('diagnosis.done_status', { fallback: '진단이 끝났어요. 아래에서 결과를 확인하세요.' })
                : ''
          }
        />

        {diagnosisMutation.isPending && (
          <p className="entry-pending muted">
            <span className="dots" aria-hidden="true">
              <i />
              <i />
              <i />
            </span>
            {t('diagnosis.running_note', { fallback: '보유 종목을 분석하고 있어요. 1분 정도 걸릴 수 있어요.' })}
          </p>
        )}
        {diagnosisMutation.error && (
          <ErrorNotice error={diagnosisMutation.error} fallback={t('diagnosis.failed', { fallback: '진단에 실패했어요.' })} />
        )}

        {result && (
          <>
            <DiagnosisResultView result={result} />
            <div className="diagnosis-history-link">
              <span className="muted small">
                {diagnosisId != null
                  ? t('diagnosis.saved', { fallback: '이 진단은 이력에 저장됐어요.' })
                  : t('diagnosis.not_saved', { fallback: '이력에 저장되지 않았어요(저장만 실패, 결과는 정상이에요).' })}
              </span>
              {diagnosisId != null && (
                <button type="button" className="reset-button" aria-expanded={showCompare} onClick={() => setShowCompare((v) => !v)}>
                  {showCompare ? t('diagnosis.compare_close', { fallback: '비교 닫기' }) : t('history.compare', { fallback: '이전 진단과 비교' })}
                </button>
              )}
            </div>
            {showCompare && (
              <div className="diagnosis-compare-panel">
                {compareQuery.isPending && <p className="muted">{t('diagnosis.comparing', { fallback: '비교하는 중…' })}</p>}
                {compareQuery.error && <ErrorNotice error={compareQuery.error} onRetry={() => void compareQuery.refetch()} />}
                {compareQuery.data && <DiagnosisCompareView compare={compareQuery.data} />}
              </div>
            )}
          </>
        )}
      </div>
    </section>
  )
}
