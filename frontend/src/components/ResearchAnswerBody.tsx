import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { api } from '../api'
import type { TranslationStatus } from '../api/contracts/common'
import { isLocale } from '../api/contracts/common'
import { queryKeys } from '../api/queryKeys'
import { LOCALE_NATIVE_NAMES } from '../i18n'
import { useLocale } from '../i18n/useLocale'
import AnswerSections from './AnswerSections'
import Banner from './ui/Banner'
import ErrorNotice from './ui/ErrorNotice'
import StatusBadge from './ui/StatusBadge'

// 리서치 답변 본문 + 작성 언어·번역 상태 표시(핸드오프 15-1절).
//   null            ko-KR 답(번역 안 함)               -> 그대로
//   localized       코드가 그 언어로 바로 쓴 답           -> "작성 언어" 배지만
//   translated      검증된 한국어 답을 보호 번역          -> "번역문" 안내 + 한국어 원문 보기
//   fallback_summary 번역 검증 실패(그 언어 안내 + 데이터 요약만) -> 경고 + 한국어 원문 보기(report_id가 있을 때만)
//   fallback_original 옛 기록의 번역 실패(한국어 원문이 이미 들어 있음) -> 안내만
// 한국어 원문은 기록 조회(view=record)의 original_answer라서, 아직 모르면 버튼을 누를 때 그때 불러온다.
export default function ResearchAnswerBody({
  answer,
  locale,
  translationStatus,
  reportId,
  originalAnswer,
}: {
  answer: string
  locale?: string
  translationStatus?: TranslationStatus | null
  reportId: number | null
  // 이미 알고 있으면(기록 상세) 다시 불러오지 않는다
  originalAnswer?: string | null
}) {
  const { t, locale: uiLocale } = useLocale()
  const [showOriginal, setShowOriginal] = useState(false)

  const canFetchOriginal = originalAnswer === undefined && reportId != null && showOriginal
  const record = useQuery({
    queryKey: queryKeys.reportRecord(reportId ?? -1),
    queryFn: () => api.getReportRecord(reportId!),
    enabled: canFetchOriginal,
  })
  const original = originalAnswer ?? record.data?.original_answer ?? null

  const writtenIn = locale && isLocale(locale) && locale !== 'ko-KR' ? LOCALE_NATIVE_NAMES[locale] : null
  // 원문이 있을 수 있는 상태(번역·요약 대체)이고, 다시 볼 방법(report_id 또는 이미 아는 원문)이 있을 때만 버튼을 보인다.
  // fallback_summary인데 report_id가 null(저장 실패)이면 원문을 다시 볼 방법이 없어 버튼을 숨긴다.
  const canShowOriginal =
    (translationStatus === 'translated' || translationStatus === 'fallback_summary') && (originalAnswer != null || reportId != null)

  return (
    <div className="research-answer">
      {(translationStatus != null || writtenIn) && (
        <div className="translation-notice">
          {writtenIn && uiLocale !== locale && <StatusBadge tone="accent">{t('common.written_in', { vars: { language: writtenIn } })}</StatusBadge>}
          {translationStatus === 'translated' && <span className="muted small">{t('research.translation_translated')}</span>}
          {translationStatus === 'localized' && writtenIn && <span className="muted small">{writtenIn}</span>}
          {canShowOriginal && (
            <button type="button" className="link-button" aria-pressed={showOriginal} onClick={() => setShowOriginal((v) => !v)}>
              {showOriginal ? t('research.hide_original', { fallback: '번역문 보기' }) : t('research.show_original')}
            </button>
          )}
        </div>
      )}

      {translationStatus === 'fallback_summary' && (
        <Banner tone="warn">{t('research.fallback_summary_notice', { fallback: '번역을 검증하지 못해 데이터 요약만 보여드려요. 한국어 원문을 볼 수 있어요.' })}</Banner>
      )}
      {translationStatus === 'fallback_original' && <Banner tone="info">{t('research.translation_fallback')}</Banner>}

      {showOriginal && canShowOriginal ? (
        <>
          {record.isPending && originalAnswer === undefined && <p className="muted small">{t('common.loading')}</p>}
          {record.error && <ErrorNotice error={record.error} onRetry={() => void record.refetch()} />}
          {original != null ? (
            <AnswerSections answer={original} />
          ) : (
            !record.isPending && !record.error && <p className="muted small">{t('common.calculation_unavailable', { fallback: '원문을 불러오지 못했어요.' })}</p>
          )}
        </>
      ) : (
        <AnswerSections answer={answer} />
      )}
    </div>
  )
}
