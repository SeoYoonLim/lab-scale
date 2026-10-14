import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { api } from '../../api'
import { queryKeys } from '../../api/queryKeys'
import DiagnosisCompareView from '../../components/DiagnosisCompareView'
import DiagnosisResultView from '../../components/DiagnosisResultView'

// 진단은 "항상 새로 계산"이고 결과를 캐시해서 재사용할 이유가 없다(backend/API.md) - 쿼리가 아니라
// 뮤테이션(버튼을 누르는 행동)으로 다룬다. 보유 종목이 바뀌면 부모가 key를 바꿔 이 컴포넌트를
// 통째로 리마운트시켜서 지난 결과를 지운다.
export default function DiagnosisSection() {
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
      <h2 className="section-title">AI 포트폴리오 진단</h2>
      <div className="card">
        <div className="card-head">
          <p className="muted">보유 종목을 바탕으로 비중·손익·위험 요인을 진단해드려요.</p>
          <button
            type="button"
            className="send"
            onClick={() => diagnosisMutation.mutate()}
            disabled={diagnosisMutation.isPending}
          >
            {diagnosisMutation.isPending ? '진단 중…' : 'AI 진단 받기'}
          </button>
        </div>

        {diagnosisMutation.isPending && (
          <p className="entry-pending muted">
            <span className="dots" aria-hidden="true">
              <i />
              <i />
              <i />
            </span>
            보유 종목을 분석하고 있어요. 1분 정도 걸릴 수 있어요.
          </p>
        )}
        {diagnosisMutation.error && (
          <p className="notice notice-error">
            {diagnosisMutation.error instanceof Error ? diagnosisMutation.error.message : '진단에 실패했어요.'}
          </p>
        )}

        {result && (
          <>
            <DiagnosisResultView result={result} />
            <div className="diagnosis-history-link">
              <span className="muted small">
                {diagnosisId != null ? '이 진단은 이력에 저장됐어요.' : '이력에 저장되지 않았어요(저장만 실패, 결과는 정상이에요).'}
              </span>
              {diagnosisId != null && (
                <button type="button" className="reset-button" onClick={() => setShowCompare((v) => !v)}>
                  {showCompare ? '비교 닫기' : '이전 진단과 비교'}
                </button>
              )}
            </div>
            {showCompare && (
              <div className="diagnosis-compare-panel">
                {compareQuery.isPending && <p className="muted">비교하는 중…</p>}
                {compareQuery.error && <p className="notice notice-error">{compareQuery.error.message}</p>}
                {compareQuery.data && <DiagnosisCompareView compare={compareQuery.data} />}
              </div>
            )}
          </>
        )}
      </div>
    </section>
  )
}
