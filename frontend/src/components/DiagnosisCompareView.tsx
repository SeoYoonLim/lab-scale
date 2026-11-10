import type { MetricChange } from '../api/contracts/diagnosis'
import { useLocale } from '../i18n/useLocale'
import type { DiagnosisCompareResponse } from '../types'
import { formatDate, formatPct, formatPrice, formatSignedPp, trendClass } from '../utils/format'
import Banner from './ui/Banner'

const STATUS_KEY = { added: 'diagnosis.status_added', removed: 'diagnosis.status_removed', kept: 'diagnosis.status_kept' } as const
const STATUS_FALLBACK = { added: '신규 편입', removed: '전량 제외', kept: '유지' } as const

// 지표 하나의 "이전 → 현재 (변화)". 하나라도 null이면 변화를 계산하지 않고 "—"로 둔다(0으로 만들지 않는다).
function MetricRow({ label, change, unit, digits = 2 }: { label: string; change: MetricChange; unit: string; digits?: number }) {
  const fmt = (v: number | null) => (v == null ? '—' : `${v.toFixed(digits)}${unit}`)
  return (
    <li>
      {label}: <span className="num">{fmt(change.previous)}</span> → <span className="num">{fmt(change.current)}</span>
      {change.change != null && (
        <span className={`num ${trendClass(change.change)}`}>
          {' '}
          ({change.change > 0 ? '+' : ''}
          {change.change.toFixed(digits)}
          {unit === '%' ? '%p' : unit})
        </span>
      )}
    </li>
  )
}

export default function DiagnosisCompareView({ compare }: { compare: DiagnosisCompareResponse }) {
  const { t } = useLocale()
  const won = t('unit.won', { fallback: '원' })
  const none = t('diagnosis.none', { fallback: '없음' })
  if (!compare.previous || !compare.changes) {
    return (
      <p className="muted">
        {t('diagnosis.no_previous', { fallback: '비교할 이전 진단이 없어요(첫 진단이거나 이전 진단이 이미 삭제됐어요).' })}
      </p>
    )
  }
  const { changes, metric_changes: metrics, exposure_changes: exposure, basis } = compare

  return (
    <div className="diagnosis-result">
      {/* 두 진단은 각각 저장 당시의 보유·시세로 계산한 값이라 기준 시점이 다르다 - 비교 화면 맨 위에 항상 보여준다 */}
      {basis?.note && <Banner tone="info">{basis.note}</Banner>}

      <div className="meta muted small">
        <span>{t('diagnosis.diagnosed_on', { fallback: '{date} 진단', vars: { date: formatDate(compare.previous.created_at) } })}</span>
        <span>→</span>
        <span>{t('diagnosis.diagnosed_on', { fallback: '{date} 진단', vars: { date: formatDate(compare.current.created_at) } })}</span>
      </div>

      <div className="diagnosis-block">
        <div className="muted small">{t('diagnosis.change_total_asset')}</div>
        <p className={`num ${trendClass(changes.total_asset.change_pct)}`}>
          {formatPrice(changes.total_asset.previous)}
          {won} → {formatPrice(changes.total_asset.current)}
          {won} ({changes.total_asset.change_amount >= 0 ? '+' : ''}
          {formatPrice(changes.total_asset.change_amount)}
          {won}
          {changes.total_asset.change_pct != null && `, ${formatPct(changes.total_asset.change_pct)}`})
        </p>
      </div>

      {metrics && (
        <div className="diagnosis-block">
          <div className="muted small">
            {t('diagnosis.change_cash_weight')} · {t('diagnosis.change_concentration')}
          </div>
          <ul className="list">
            <MetricRow label={t('diagnosis.cash_weight')} change={metrics.cash_weight_pct} unit="%" />
            <MetricRow label={t('diagnosis.top1_weight', { fallback: '상위 1종목 비중' })} change={metrics.top1_weight_pct} unit="%" />
            <MetricRow label={t('diagnosis.top3_weight', { fallback: '상위 3종목 비중' })} change={metrics.top3_weight_pct} unit="%" />
            <MetricRow label={t('diagnosis.herfindahl', { fallback: '집중도(허핀달)' })} change={metrics.herfindahl_index} unit="" digits={4} />
            <MetricRow label={t('diagnosis.holding_count', { fallback: '보유 종목 수' })} change={metrics.holding_count} unit="" digits={0} />
          </ul>
        </div>
      )}

      {changes.holdings.length > 0 && (
        <div className="diagnosis-block">
          <div className="muted small">{t('diagnosis.change_holdings')}</div>
          <ul className="list">
            {changes.holdings.map((h) => (
              <li key={h.ticker}>
                {h.company_name} - {t(STATUS_KEY[h.status], { fallback: STATUS_FALLBACK[h.status] })} ({h.previous_weight_pct ?? '-'}% →{' '}
                {h.current_weight_pct ?? '-'}%
                {h.weight_change_pp != null && `, ${formatSignedPp(h.weight_change_pp)}`})
              </li>
            ))}
          </ul>
        </div>
      )}

      {(changes.flags.added.length > 0 || changes.flags.resolved.length > 0) && (
        <div className="diagnosis-columns">
          <div>
            <div className="muted small">{t('diagnosis.flags_added')}</div>
            <ul className="list">
              {changes.flags.added.length === 0 && <li className="muted">{none}</li>}
              {changes.flags.added.map((f, i) => (
                <li key={i}>{f.message}</li>
              ))}
            </ul>
          </div>
          <div>
            <div className="muted small">{t('diagnosis.flags_resolved')}</div>
            <ul className="list">
              {changes.flags.resolved.length === 0 && <li className="muted">{none}</li>}
              {changes.flags.resolved.map((f, i) => (
                <li key={i}>{f.message}</li>
              ))}
            </ul>
          </div>
        </div>
      )}

      {changes.returns_20d.length > 0 && (
        <div className="diagnosis-block">
          <div className="muted small">{t('diagnosis.returns20_change', { fallback: '20거래일 수익률 변화' })}</div>
          <ul className="list">
            {changes.returns_20d.map((r) => (
              <li key={r.ticker}>
                {r.company_name}: {formatPct(r.previous_return_20d_pct)} → {formatPct(r.current_return_20d_pct)}
                {r.change_pp != null && ` (${formatSignedPp(r.change_pp)})`}
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* ETF 실질 노출 변화: 두 진단 모두 스냅샷(status ok)이 있을 때만 온다. 한쪽이 옛 진단이면 null이라 비교하지 않는다. */}
      {exposure ? (
        <div className="diagnosis-block">
          <div className="muted small">{t('look_through.title')}</div>
          <ul className="list">
            {exposure.companies.slice(0, 8).map((c) => (
              <li key={`${c.code}-${c.name}`}>
                {c.name} - {t(`diagnosis.status_${c.status === 'changed' ? 'kept' : c.status}`, { fallback: c.status })} ({c.previous_total_weight_pct ?? '—'}% →{' '}
                {c.current_total_weight_pct ?? '—'}%
                {c.change_pct != null && `, ${formatSignedPp(c.change_pct)}`})
              </li>
            ))}
          </ul>
          <p className="muted small">
            {t('diagnosis.exposure_compare_note', { fallback: '비교 대상 기업은 각 진단에 저장된 상위 20개끼리예요.' })}
          </p>
        </div>
      ) : (
        <p className="muted small">{t('diagnosis.exposure_not_compared')}</p>
      )}

      <p className="disclaimer-note muted small">{compare.disclaimer}</p>
    </div>
  )
}
