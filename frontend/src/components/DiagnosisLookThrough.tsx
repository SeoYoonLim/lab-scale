import type { LookThroughSnapshot } from '../api/contracts/exposure'
import { useLocale } from '../i18n/useLocale'
import { formatTradingDate } from '../utils/format'
import LookThroughPanels from './LookThroughPanels'
import Banner from './ui/Banner'

// 진단 결과에 붙은 ETF 실질 노출 스냅샷(2026-11-06). 진단 당시 값 그대로라서, 과거 진단을 다시 열어도 지금 구성종목으로
// 다시 계산하지 않는다. 이 기능 전에 저장한 진단은 look_through가 null이다(핸드오프 6-1절).
export default function DiagnosisLookThrough({ snapshot }: { snapshot: LookThroughSnapshot | null | undefined }) {
  const { t } = useLocale()

  if (snapshot == null) {
    return (
      <p className="muted small">
        {t('diagnosis.lt_no_snapshot', {
          fallback: '이 진단에는 ETF 실질 노출 기록이 없어요(실질 노출 기능이 생기기 전에 저장된 진단이에요).',
        })}
      </p>
    )
  }
  if (snapshot.status === 'unavailable') {
    return (
      <Banner tone="info">
        {t('diagnosis.lt_unavailable', { fallback: '실질 노출을 계산하지 못했어요. 진단의 나머지 결과는 정상이에요.' })}
      </Banner>
    )
  }
  if (snapshot.status !== 'ok') {
    return (
      <p className="muted small">
        {t('diagnosis.lt_not_ok', { fallback: '보유 종목의 종가가 없거나 보유가 없어서 실질 노출을 계산하지 않았어요.' })}
      </p>
    )
  }

  return (
    <details className="diagnosis-look-through">
      <summary>
        {t('look_through.title')}{' '}
        <span className="muted small">
          {t('diagnosis.lt_basis', {
            fallback: '· 진단 당시 {date} 종가 기준',
            vars: { date: formatTradingDate(snapshot.valuation_date) },
          })}
        </span>
      </summary>
      {snapshot.etf_holdings_stale && (
        <Banner tone="warn">
          {t('look_through.stale_badge')}: {t('exposure.etf_stale_note', { fallback: 'ETF 구성종목 기준일이 오래됐어요. 현재 구성과 다를 수 있어요.' })}
        </Banner>
      )}
      <LookThroughPanels body={snapshot} compact />
    </details>
  )
}
