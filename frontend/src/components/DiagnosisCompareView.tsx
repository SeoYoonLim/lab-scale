import type { DiagnosisCompareResponse } from '../types'
import { formatDate, formatPct, formatPrice, trendClass } from '../utils/format'

const STATUS_LABEL = { added: '신규 편입', removed: '전량 제외', kept: '유지' } as const

export default function DiagnosisCompareView({ compare }: { compare: DiagnosisCompareResponse }) {
  if (!compare.previous || !compare.changes) {
    return <p className="muted">비교할 이전 진단이 없어요(첫 진단이거나 이전 진단이 이미 삭제됐어요).</p>
  }
  const { changes } = compare

  return (
    <div className="diagnosis-result">
      <div className="meta muted small">
        <span>{formatDate(compare.previous.created_at)} 진단</span>
        <span>→</span>
        <span>{formatDate(compare.current.created_at)} 진단</span>
      </div>

      <div className="diagnosis-block">
        <div className="muted small">총자산 변화</div>
        <p className={`num ${trendClass(changes.total_asset.change_pct)}`}>
          {formatPrice(changes.total_asset.previous)}원 → {formatPrice(changes.total_asset.current)}원 (
          {changes.total_asset.change_amount >= 0 ? '+' : ''}
          {formatPrice(changes.total_asset.change_amount)}원
          {changes.total_asset.change_pct != null && `, ${formatPct(changes.total_asset.change_pct)}`})
        </p>
      </div>

      {changes.holdings.length > 0 && (
        <div className="diagnosis-block">
          <div className="muted small">보유 비중 변화</div>
          <ul className="list">
            {changes.holdings.map((h) => (
              <li key={h.ticker}>
                {h.company_name} - {STATUS_LABEL[h.status]} ({h.previous_weight_pct ?? '-'}% →{' '}
                {h.current_weight_pct ?? '-'}%
                {h.weight_change_pp != null && `, ${formatPct(h.weight_change_pp)}p`})
              </li>
            ))}
          </ul>
        </div>
      )}

      {(changes.flags.added.length > 0 || changes.flags.resolved.length > 0) && (
        <div className="diagnosis-columns">
          <div>
            <div className="muted small">새로 생긴 위험</div>
            <ul className="list">
              {changes.flags.added.length === 0 && <li className="muted">없음</li>}
              {changes.flags.added.map((f, i) => (
                <li key={i}>{f.message}</li>
              ))}
            </ul>
          </div>
          <div>
            <div className="muted small">해소된 위험</div>
            <ul className="list">
              {changes.flags.resolved.length === 0 && <li className="muted">없음</li>}
              {changes.flags.resolved.map((f, i) => (
                <li key={i}>{f.message}</li>
              ))}
            </ul>
          </div>
        </div>
      )}

      {changes.returns_20d.length > 0 && (
        <div className="diagnosis-block">
          <div className="muted small">20거래일 수익률 변화</div>
          <ul className="list">
            {changes.returns_20d.map((r) => (
              <li key={r.ticker}>
                {r.company_name}: {formatPct(r.previous_return_20d_pct)} → {formatPct(r.current_return_20d_pct)}
                {r.change_pp != null && ` (${formatPct(r.change_pp)}p)`}
              </li>
            ))}
          </ul>
        </div>
      )}

      <p className="disclaimer-note muted small">{compare.disclaimer}</p>
    </div>
  )
}
