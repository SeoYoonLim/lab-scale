import { useLocale } from '../i18n/useLocale'
import type { Source, SourceStatus } from '../types'
import { formatDate } from '../utils/format'

// 근거 문서(sources)가 비었을 때 그 이유를 한 줄로 알린다(백엔드 source_status, backend/app/sources.py).
// - 근거가 있으면(sources > 0) 아무것도 그리지 않는다: 출처 카드(SourceFootnotes)가 보인다.
// - 뉴스·공시를 조회하지 않는 답(not_applicable: 시세·계산·안내)과 status가 없을 때도 그리지 않는다(답 본문의 설명을 그대로 둔다).
// - 백엔드 message는 한국어라 쓰지 않고 status별 문구를 언어마다 고른다. 모르는 status·내부 코드·예외 문자열은 보이지 않는다.
// 출처를 만들어 보여주지 않는다(링크 없음).
const KNOWN = ['none_found', 'excluded_after_cutoff', 'long_period', 'error'] as const
type Known = (typeof KNOWN)[number]

export default function SourceStatusNote({ sources, status }: { sources: Source[]; status?: SourceStatus | null }) {
  const { t } = useLocale()
  if (sources.length > 0 || !status) return null
  const code = status.status as Known
  if (!KNOWN.includes(code)) return null
  const excluded = (status.excluded_after_cutoff_count ?? 0) + (status.excluded_unknown_time_count ?? 0)
  const text = {
    none_found: t('sources.empty_none_found', {
      fallback: '분석 기간 안에 이 종목을 직접 다룬 뉴스·공시를 찾지 못해 출처가 없어요. 원인을 특정하지 않았어요.',
    }),
    excluded_after_cutoff: t('sources.empty_after_cutoff', {
      fallback:
        '시세 기준일({date}) 장 마감 뒤 자료 {n}건은 그 시세에 반영되지 않아 근거로 쓰지 않았고, 그 전의 근거가 없어 출처가 없어요. 원인을 특정하지 않았어요.',
      vars: { date: formatDate(status.market_data_as_of ?? null), n: excluded.toLocaleString() },
    }),
    long_period: t('sources.empty_long_period', {
      fallback: '기간이 3개월보다 길어 기사·공시를 원인 근거로 고르지 않았어요. 출처 목록이 없어요.',
    }),
    error: t('sources.empty_error', {
      fallback: '뉴스·공시를 조회하는 중 문제가 생겨 출처를 가져오지 못했어요. 잠시 뒤 다시 시도해 주세요.',
    }),
  }[code]
  return (
    <p className={status.is_error ? 'source-status-note notice small' : 'source-status-note muted small'} data-status={code}>
      {text}
    </p>
  )
}
