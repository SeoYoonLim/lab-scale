import { useState } from 'react'
import type { FormEvent } from 'react'
import { api } from '../api'
import { useAsync } from '../hooks/useAsync'
import type { DiagnosisResponse, Portfolio, WatchlistItem } from '../types'
import { formatDate, formatPct, formatPrice, trendClass } from '../utils/format'

const TRADES_PAGE_SIZE = 20

function WatchlistSection() {
  const [reloadKey, setReloadKey] = useState(0)
  const { data, error, loading } = useAsync(() => api.listWatchlist(), [reloadKey])
  const [ticker, setTicker] = useState('')
  const [adding, setAdding] = useState(false)
  const [formError, setFormError] = useState<string | null>(null)

  async function handleAdd(e: FormEvent) {
    e.preventDefault()
    if (!ticker.trim() || adding) return
    setAdding(true)
    setFormError(null)
    try {
      await api.addWatchlistItem(ticker.trim())
      setTicker('')
      setReloadKey((k) => k + 1)
    } catch (err) {
      setFormError(err instanceof Error ? err.message : '추가하지 못했어요.')
    } finally {
      setAdding(false)
    }
  }

  async function handleRemove(item: WatchlistItem) {
    try {
      await api.removeWatchlistItem(item.ticker)
      setReloadKey((k) => k + 1)
    } catch (err) {
      setFormError(err instanceof Error ? err.message : '삭제하지 못했어요.')
    }
  }

  return (
    <section>
      <h2 className="section-title">관심종목</h2>
      <div className="card">
        <form className="inline-form" onSubmit={handleAdd}>
          <input
            value={ticker}
            onChange={(e) => setTicker(e.target.value)}
            placeholder="종목명 또는 종목코드 (예: 삼성전자)"
            aria-label="관심종목에 추가할 종목"
          />
          <button type="submit" disabled={adding || !ticker.trim()}>
            추가
          </button>
        </form>
        {formError && <p className="notice notice-error">{formError}</p>}

        {loading && !data && <p className="muted">불러오는 중…</p>}
        {error && <p className="notice notice-error">{error.message}</p>}
        {data && data.length === 0 && <p className="muted">아직 등록한 관심종목이 없어요.</p>}

        <ul className="ledger">
          {data?.map((item) => (
            <li key={item.ticker} className="ledger-row watch-row">
              <div>
                <div className="ledger-question">{item.company_name}</div>
                <div className="meta muted small">
                  <span>{item.ticker}</span>
                  <span>{formatDate(item.added_at)} 등록</span>
                </div>
              </div>
              <div className="watch-right">
                {item.latest_close != null ? (
                  <div className="meta meta-end">
                    <span className="num">{formatPrice(item.latest_close)}원</span>
                    <span className={`num ${trendClass(item.change_pct)}`}>{formatPct(item.change_pct)}</span>
                  </div>
                ) : (
                  <span className="muted small">가격 없음</span>
                )}
                <button type="button" className="delete-report" onClick={() => void handleRemove(item)}>
                  삭제
                </button>
              </div>
            </li>
          ))}
        </ul>
      </div>
    </section>
  )
}

function HoldingsSection({ portfolio, onChange }: { portfolio: Portfolio; onChange: () => void }) {
  const [ticker, setTicker] = useState('')
  const [side, setSide] = useState<'buy' | 'sell'>('buy')
  const [quantity, setQuantity] = useState('1')
  const [pending, setPending] = useState(false)
  const [resetting, setResetting] = useState(false)
  const [formError, setFormError] = useState<string | null>(null)

  async function handleOrder(e: FormEvent) {
    e.preventDefault()
    const qty = Number(quantity)
    if (!ticker.trim() || !qty || qty <= 0 || pending) return
    setPending(true)
    setFormError(null)
    try {
      await api.placeOrder(ticker.trim(), side, qty)
      setTicker('')
      setQuantity('1')
      onChange()
    } catch (err) {
      setFormError(err instanceof Error ? err.message : '주문에 실패했어요.')
    } finally {
      setPending(false)
    }
  }

  async function handleReset() {
    if (
      !confirm(
        '모의투자를 초기화할까요? 보유 종목과 체결 내역이 모두 삭제되고 잔고가 1,000만원으로 돌아가요. ' +
          '관심종목·리서치 기록은 그대로 남고, 되돌릴 수 없어요.',
      )
    ) {
      return
    }
    setResetting(true)
    setFormError(null)
    try {
      await api.resetPortfolio()
      onChange()
    } catch (err) {
      setFormError(err instanceof Error ? err.message : '초기화하지 못했어요.')
    } finally {
      setResetting(false)
    }
  }

  return (
    <section>
      <h2 className="section-title">모의투자</h2>
      <div className="card">
        <div className="portfolio-summary">
          <div>
            <div className="muted small">현금</div>
            <div className="num price-big">{formatPrice(portfolio.cash_balance)}원</div>
          </div>
          <div>
            <div className="muted small">평가금액</div>
            <div className="num price-big">{formatPrice(portfolio.total_eval_amount)}원</div>
          </div>
          <div>
            <div className="muted small">총 자산</div>
            <div className="num price-big">{formatPrice(portfolio.total_asset)}원</div>
          </div>
        </div>

        <div className="portfolio-actions">
          <button type="button" className="reset-button" onClick={() => void handleReset()} disabled={resetting}>
            {resetting ? '초기화 중…' : '초기화'}
          </button>
        </div>

        {portfolio.note && <p className="notice">{portfolio.note}</p>}

        <form className="order-form" onSubmit={handleOrder}>
          <input
            value={ticker}
            onChange={(e) => setTicker(e.target.value)}
            placeholder="종목명 또는 종목코드"
            aria-label="주문할 종목"
          />
          <select value={side} onChange={(e) => setSide(e.target.value as 'buy' | 'sell')} aria-label="매수/매도">
            <option value="buy">매수</option>
            <option value="sell">매도</option>
          </select>
          <input
            type="number"
            min={1}
            value={quantity}
            onChange={(e) => setQuantity(e.target.value)}
            aria-label="수량"
          />
          <button type="submit" disabled={pending}>
            {pending ? '주문 중…' : '주문'}
          </button>
        </form>
        {formError && <p className="notice notice-error">{formError}</p>}

        {portfolio.holdings.length === 0 ? (
          <p className="muted">보유 종목이 없어요.</p>
        ) : (
          <ul className="ledger">
            {portfolio.holdings.map((h) => (
              <li key={h.ticker} className="ledger-row watch-row">
                <div>
                  <div className="ledger-question">{h.company_name}</div>
                  <div className="meta muted small">
                    <span>{h.quantity}주</span>
                    <span>평균 {formatPrice(h.avg_price)}원</span>
                  </div>
                </div>
                <div className="watch-right">
                  <div className="meta meta-end">
                    <span className="num">{h.current_price != null ? `${formatPrice(h.current_price)}원` : '-'}</span>
                    <span className={`num ${trendClass(h.profit_loss_pct)}`}>
                      {h.profit_loss != null ? `${formatPrice(h.profit_loss)}원 (${formatPct(h.profit_loss_pct)})` : '-'}
                    </span>
                  </div>
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  )
}

function DiagnosisSection() {
  const [result, setResult] = useState<DiagnosisResponse | null>(null)
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function handleDiagnose() {
    setPending(true)
    setError(null)
    try {
      setResult(await api.getDiagnosis())
    } catch (err) {
      setError(err instanceof Error ? err.message : '진단에 실패했어요.')
    } finally {
      setPending(false)
    }
  }

  return (
    <section>
      <h2 className="section-title">AI 포트폴리오 진단</h2>
      <div className="card">
        <div className="card-head">
          <p className="muted">보유 종목을 바탕으로 비중·손익·위험 요인을 진단해드려요.</p>
          <button type="button" className="send" onClick={() => void handleDiagnose()} disabled={pending}>
            {pending ? '진단 중…' : 'AI 진단 받기'}
          </button>
        </div>

        {pending && (
          <p className="entry-pending muted">
            <span className="dots" aria-hidden="true">
              <i />
              <i />
              <i />
            </span>
            보유 종목을 분석하고 있어요. 1분 정도 걸릴 수 있어요.
          </p>
        )}
        {error && <p className="notice notice-error">{error}</p>}

        {result && (
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
        )}
      </div>
    </section>
  )
}

function TradesSection({ reloadKey }: { reloadKey: number }) {
  const [offset, setOffset] = useState(0)
  const [tickerFilter, setTickerFilter] = useState('')
  const [appliedFilter, setAppliedFilter] = useState('')
  const { data, error, loading } = useAsync(
    () => api.getTrades(TRADES_PAGE_SIZE, offset, appliedFilter || undefined),
    [offset, appliedFilter, reloadKey],
  )

  function handleFilterSubmit(e: FormEvent) {
    e.preventDefault()
    setOffset(0)
    setAppliedFilter(tickerFilter.trim())
  }

  return (
    <section>
      <h2 className="section-title">체결 내역</h2>
      <div className="card">
        <form className="inline-form" onSubmit={handleFilterSubmit}>
          <input
            value={tickerFilter}
            onChange={(e) => setTickerFilter(e.target.value)}
            placeholder="종목명으로 필터 (예: 삼성전자)"
            aria-label="체결 내역 종목 필터"
          />
          <button type="submit">필터</button>
        </form>

        {loading && !data && <p className="muted">불러오는 중…</p>}
        {error && <p className="notice notice-error">{error.message}</p>}
        {data && data.items.length === 0 && <p className="muted">체결 내역이 없어요.</p>}

        {data && data.items.length > 0 && (
          <ul className="ledger">
            {data.items.map((t) => (
              <li key={t.id} className="ledger-row watch-row">
                <div>
                  <div className="ledger-question">{t.company_name}</div>
                  <div className="meta muted small">
                    <span>{t.side === 'buy' ? '매수' : '매도'}</span>
                    <span>{t.quantity}주</span>
                    <span>{formatPrice(t.price)}원</span>
                    <span>{formatDate(t.executed_at)}</span>
                  </div>
                </div>
                <div className="watch-right">
                  <span className="num">{formatPrice(t.amount)}원</span>
                </div>
              </li>
            ))}
          </ul>
        )}

        {data && data.total > 0 && (
          <div className="pagination">
            <button
              type="button"
              disabled={offset === 0}
              onClick={() => setOffset(Math.max(0, offset - TRADES_PAGE_SIZE))}
            >
              이전
            </button>
            <span className="muted small">
              {offset + 1}–{Math.min(offset + TRADES_PAGE_SIZE, data.total)} / {data.total}
            </span>
            <button
              type="button"
              disabled={offset + TRADES_PAGE_SIZE >= data.total}
              onClick={() => setOffset(offset + TRADES_PAGE_SIZE)}
            >
              다음
            </button>
          </div>
        )}
      </div>
    </section>
  )
}

export default function PortfolioPage() {
  const [reloadKey, setReloadKey] = useState(0)
  const { data, error, loading } = useAsync(() => api.getPortfolio(), [reloadKey])

  return (
    <div className="container page portfolio-page">
      <h1 className="page-title">포트폴리오</h1>

      {loading && !data && <p className="muted">불러오는 중…</p>}
      {error && <p className="notice notice-error">{error.message}</p>}
      {data && <HoldingsSection portfolio={data} onChange={() => setReloadKey((k) => k + 1)} />}

      <DiagnosisSection />
      <TradesSection reloadKey={reloadKey} />
      <WatchlistSection />
    </div>
  )
}
