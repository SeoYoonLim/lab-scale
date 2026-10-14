import type { DiagnosisDetail, DiagnosisResponse } from '../types'
import { formatDate, formatPct, formatPrice, trendClass } from '../utils/format'

// 현재 진단(POST 응답)과 이력 상세(GET /diagnosis/{id})가 같은 모양이라 하나로 그린다
// (backend/API.md: "진단 결과 화면을 POST 응답과 같은 코드로 그릴 수 있다").
export default function DiagnosisResultView({ result }: { result: DiagnosisResponse | DiagnosisDetail }) {
  return (
    <div className="diagnosis-result">
      <div className="meta">
        <span className="diagnosis-source">
          {result.source === 'llm' ? `AI 설명 (${result.model})` : '규칙 기반 설명'}
        </span>
        <span className="muted small">{formatDate(result.generated_at)} 기준</span>
      </div>

      <p className="entry-answer">{result.summary}</p>

      <div className="diagnosis-metrics">
        <div>
          <span className="muted small">총자산</span>
          <span className="num">{formatPrice(result.metrics.total_asset)}원</span>
        </div>
        <div>
          <span className="muted small">현금 비중</span>
          <span className="num">{result.metrics.cash_weight_pct ?? '-'}%</span>
        </div>
        <div>
          <span className="muted small">상위 1종목 비중</span>
          <span className="num">{result.metrics.top1_weight_pct ?? '-'}%</span>
        </div>
        <div>
          <span className="muted small">상위 3종목 비중</span>
          <span className="num">{result.metrics.top3_weight_pct ?? '-'}%</span>
        </div>
        <div>
          <span className="muted small">집중도(허핀달)</span>
          <span className="num">{result.metrics.herfindahl_index ?? '-'}</span>
        </div>
        <div>
          <span className="muted small">총 평가손익</span>
          <span className={`num ${trendClass(result.metrics.total_profit_loss_pct)}`}>
            {formatPrice(result.metrics.total_profit_loss)}원 ({formatPct(result.metrics.total_profit_loss_pct)})
          </span>
        </div>
      </div>

      {result.flags.length > 0 && (
        <div className="diagnosis-block">
          <div className="muted small">확인된 항목</div>
          <ul className="list">
            {result.flags.map((flag, i) => (
              <li key={i}>{flag.message}</li>
            ))}
          </ul>
        </div>
      )}

      <div className="diagnosis-columns">
        <div>
          <div className="muted small">강점</div>
          <ul className="list">
            {result.strengths.map((s, i) => (
              <li key={i}>{s}</li>
            ))}
          </ul>
        </div>
        <div>
          <div className="muted small">위험 요인</div>
          <ul className="list">
            {result.risks.map((s, i) => (
              <li key={i}>{s}</li>
            ))}
          </ul>
        </div>
        <div>
          <div className="muted small">제안</div>
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
