import AssetTypeBadge from './AssetTypeBadge'
import DiagnosisLookThrough from './DiagnosisLookThrough'
import { useLocale } from '../i18n/useLocale'
import type { DiagnosisDetail, DiagnosisResponse } from '../types'
import { formatDate, formatPct, formatPrice, trendClass } from '../utils/format'

// market_weights_pct의 키 순서(backend/API.md: KOSPI/KOSDAQ(KOSDAQ GLOBAL 포함)/OTHER, ETF는 빼고 계산) -
// 응답이 object라 키 순서가 뒤섞이지 않게 고정 순서로 보여준다. 모르는 키가 와도 뒤에 그대로 붙는다.
const MARKET_ORDER = ['KOSPI', 'KOSDAQ', 'OTHER']
function sortedMarketEntries(weights: Record<string, number | null>) {
  return Object.entries(weights).sort(
    ([a], [b]) => (MARKET_ORDER.indexOf(a) + 1 || 99) - (MARKET_ORDER.indexOf(b) + 1 || 99),
  )
}

// 현재 진단(POST 응답)과 이력 상세(GET /diagnosis/{id})가 같은 모양이라 하나로 그린다
// (backend/API.md: "진단 결과 화면을 POST 응답과 같은 코드로 그릴 수 있다").
export default function DiagnosisResultView({ result }: { result: DiagnosisResponse | DiagnosisDetail }) {
  const { t } = useLocale()
  const won = t('unit.won', { fallback: '원' })
  return (
    <div className="diagnosis-result">
      <div className="meta">
        <span className="diagnosis-source">
          {result.source === 'llm'
            ? `${t('diagnosis.source_llm', { fallback: 'AI 설명' })} (${result.model})`
            : t('diagnosis.source_rule_based', { fallback: '규칙 기반 설명' })}
        </span>
        <span className="muted small">
          {t('diagnosis.as_of', { fallback: '{date} 기준', vars: { date: formatDate(result.generated_at) } })}
        </span>
      </div>

      <p className="entry-answer">{result.summary}</p>

      <div className="diagnosis-metrics">
        <div>
          <span className="muted small">{t('diagnosis.total_asset', { fallback: '총자산' })}</span>
          <span className="num">
            {formatPrice(result.metrics.total_asset)}
            {won}
          </span>
        </div>
        <div>
          <span className="muted small">{t('diagnosis.cash_weight', { fallback: '현금 비중' })}</span>
          <span className="num">{result.metrics.cash_weight_pct ?? '-'}%</span>
        </div>
        <div>
          <span className="muted small">{t('diagnosis.top1_weight', { fallback: '상위 1종목 비중' })}</span>
          <span className="num">{result.metrics.top1_weight_pct ?? '-'}%</span>
        </div>
        <div>
          <span className="muted small">{t('diagnosis.top3_weight', { fallback: '상위 3종목 비중' })}</span>
          <span className="num">{result.metrics.top3_weight_pct ?? '-'}%</span>
        </div>
        <div>
          <span className="muted small">{t('diagnosis.herfindahl', { fallback: '집중도(허핀달)' })}</span>
          <span className="num">{result.metrics.herfindahl_index ?? '-'}</span>
        </div>
        <div>
          <span className="muted small">{t('diagnosis.total_pl', { fallback: '총 평가손익' })}</span>
          <span className={`num ${trendClass(result.metrics.total_profit_loss_pct)}`}>
            {formatPrice(result.metrics.total_profit_loss)}
            {won} ({formatPct(result.metrics.total_profit_loss_pct)})
          </span>
        </div>
      </div>

      {/* 주식 평가금액 대비 시장별 비중(ETF 제외, backend/API.md) - MARKET_SKEW 플래그가 보는 바로 그 수치라
          플래그 메시지만으로는 "얼마나" 쏠렸는지 안 보이는 걸 보완한다. 주식이 없으면 {}라 아예 안 보여준다. */}
      {Object.keys(result.metrics.market_weights_pct).length > 0 && (
        <div className="meta muted small diagnosis-market-weights">
          <span>{t('diagnosis.market_weights', { fallback: '시장별 비중(주식 기준)' })}</span>
          {sortedMarketEntries(result.metrics.market_weights_pct).map(([market, pct]) => (
            <span key={market} className="num">
              {market} {pct ?? '-'}%
            </span>
          ))}
        </div>
      )}

      {result.holdings.length > 0 && (
        <div className="diagnosis-block">
          <div className="muted small">{t('diagnosis.holdings_title', { fallback: '보유 종목별 비중' })}</div>
          <div className="diagnosis-holdings-scroll">
            <table className="diagnosis-holdings">
              <thead>
                <tr>
                  <th scope="col">{t('diagnosis.col_stock', { fallback: '종목' })}</th>
                  <th scope="col">{t('diagnosis.col_market', { fallback: '시장' })}</th>
                  <th scope="col">{t('etf.col_weight', { fallback: '비중' })}</th>
                  <th scope="col">{t('diagnosis.col_return20', { fallback: '20거래일 수익률' })}</th>
                  <th scope="col">{t('diagnosis.col_pl', { fallback: '평가손익' })}</th>
                </tr>
              </thead>
              <tbody>
                {result.holdings.map((h) => (
                  <tr key={h.ticker}>
                    <th scope="row">
                      {h.company_name} <AssetTypeBadge assetType={h.asset_type} />
                    </th>
                    <td>{h.market}</td>
                    <td className="num">
                      {h.price_unavailable ? (
                        <span className="muted">{t('diagnosis.price_failed', { fallback: '가격 조회 실패' })}</span>
                      ) : (
                        `${h.weight_pct ?? '-'}%`
                      )}
                    </td>
                    <td className={`num ${trendClass(h.return_20d_pct)}`}>{formatPct(h.return_20d_pct)}</td>
                    <td className={`num ${trendClass(h.profit_loss_pct)}`}>
                      {h.profit_loss != null ? `${formatPrice(h.profit_loss)}${won} (${formatPct(h.profit_loss_pct)})` : '-'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      <DiagnosisLookThrough snapshot={result.look_through} />

      {result.flags.length > 0 && (
        <div className="diagnosis-block">
          <div className="muted small">{t('diagnosis.flags_title', { fallback: '확인된 항목' })}</div>
          <ul className="list">
            {result.flags.map((flag, i) => (
              <li key={i}>{flag.message}</li>
            ))}
          </ul>
        </div>
      )}

      <div className="diagnosis-columns">
        <div>
          <div className="muted small">{t('diagnosis.strengths', { fallback: '강점' })}</div>
          <ul className="list">
            {result.strengths.map((s, i) => (
              <li key={i}>{s}</li>
            ))}
          </ul>
        </div>
        <div>
          <div className="muted small">{t('diagnosis.risks', { fallback: '위험 요인' })}</div>
          <ul className="list">
            {result.risks.map((s, i) => (
              <li key={i}>{s}</li>
            ))}
          </ul>
        </div>
        <div>
          <div className="muted small">{t('diagnosis.suggestions', { fallback: '제안' })}</div>
          <ul className="list">
            {result.suggestions.map((s, i) => (
              <li key={i}>{s}</li>
            ))}
          </ul>
        </div>
      </div>

      {result.notes.length > 0 && (
        <div className="notice">
          {result.notes.map((note, i) => (
            <div key={i}>{note}</div>
          ))}
        </div>
      )}

      <p className="disclaimer-note muted small">{result.disclaimer}</p>
    </div>
  )
}
