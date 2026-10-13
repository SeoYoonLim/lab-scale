import { useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import { api } from '../api'
import { ApiError } from '../api/errors'
import { useAsync } from '../hooks/useAsync'
import type { DiagnosisResponse, OrderResult, Portfolio, WatchlistItem } from '../types'
import { formatDate, formatPct, formatPrice, trendClass } from '../utils/format'

const ORDER_PREVIEW_DEBOUNCE_MS = 400

const TRADES_PAGE_SIZE = 20
const MAX_ORDER_QUANTITY = 100_000 // backend/API.md: 주문 수량은 1~100,000주(정수)

function WatchlistSection() {
  const [reloadKey, setReloadKey] = useState(0)
  const { data, error, loading } = useAsync(() => api.listWatchlist(), [reloadKey])
  const [ticker, setTicker] = useState('')
  const [adding, setAdding] = useState(false)
  const [formError, setFormError] = useState<string | null>(null)
  const [formNotice, setFormNotice] = useState<string | null>(null)

  async function handleAdd(e: FormEvent) {
    e.preventDefault()
    if (!ticker.trim() || adding) return
    setAdding(true)
    setFormError(null)
    setFormNotice(null)
    try {
      await api.addWatchlistItem(ticker.trim())
      setTicker('')
      setReloadKey((k) => k + 1)
    } catch (err) {
      // CompanyPage의 "이미 관심종목이에요"와 똑같이, 이미 등록된 종목(409)은 에러가 아니라
      // 안내로 보여준다 - 메시지 자체는 서버/mock이 주는 문구를 그대로 쓴다.
      if (err instanceof ApiError && err.status === 409) {
        setFormNotice(err.message)
      } else {
        setFormError(err instanceof Error ? err.message : '추가하지 못했어요.')
      }
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
        {formNotice && <p className="muted small">{formNotice}</p>}

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
  // 주문과 초기화가 동시에 들어가면 보유 종목이 꼬일 수 있어서(초기화 중에 주문이 체결되는 등),
  // 둘 중 하나가 진행 중이면 다른 쪽도 같이 막는다 - "어떤 작업이 진행 중인가"를 하나로 합쳤다.
  const [mutating, setMutating] = useState<'order' | 'reset' | null>(null)
  const [formError, setFormError] = useState<string | null>(null)
  const [preview, setPreview] = useState<{ companyName: string; price: number } | null>(null)
  const [lastOrder, setLastOrder] = useState<OrderResult | null>(null)

  // 입력한 종목의 실시간 시세로 "예상 금액"을 미리 보여준다 - 실제 체결가는 주문 시점 시세라 다를 수 있다.
  // company_resolver가 별칭/종목명도 받으므로 그대로 getRealtimePrice에 넘긴다.
  useEffect(() => {
    const trimmed = ticker.trim()
    // ticker가 비면 아무것도 예약하지 않는다 - preview는 그대로 둬도 아래 렌더링이 ticker.trim()을
    // 같이 확인하므로 화면에는 안 보인다(effect 안에서 수동 setState(null) 하는 대신).
    if (!trimmed) return
    let cancelled = false
    const id = setTimeout(() => {
      api.getRealtimePrice(trimmed).then(
        (data) => {
          if (!cancelled) setPreview({ companyName: data.company_name, price: data.current_price })
        },
        () => {
          // 타이핑 중이거나 모르는 종목일 수 있다 - 미리보기는 조용히 숨기고, 실제 오류는 제출 시 보여준다.
          if (!cancelled) setPreview(null)
        },
      )
    }, ORDER_PREVIEW_DEBOUNCE_MS)
    return () => {
      cancelled = true
      clearTimeout(id)
    }
  }, [ticker])

  async function handleOrder(e: FormEvent) {
    e.preventDefault()
    const qty = Number(quantity)
    if (!ticker.trim() || mutating) return
    if (!Number.isInteger(qty) || qty < 1 || qty > MAX_ORDER_QUANTITY) {
      setFormError(`수량은 1~${MAX_ORDER_QUANTITY.toLocaleString('ko-KR')} 사이의 정수여야 해요.`)
      return
    }
    setMutating('order')
    setFormError(null)
    try {
      const result = await api.placeOrder(ticker.trim(), side, qty)
      setLastOrder(result)
      setTicker('')
      setQuantity('1')
      onChange()
    } catch (err) {
      setLastOrder(null)
      setFormError(err instanceof Error ? err.message : '주문에 실패했어요.')
    } finally {
      setMutating(null)
    }
  }

  async function handleReset() {
    if (mutating) return
    if (
      !confirm(
        '모의투자를 초기화할까요? 보유 종목과 체결 내역이 모두 삭제되고 잔고가 1,000만원으로 돌아가요. ' +
          '관심종목·리서치 기록은 그대로 남고, 되돌릴 수 없어요.',
      )
    ) {
      return
    }
    setMutating('reset')
    setFormError(null)
    try {
      await api.resetPortfolio()
      setLastOrder(null) // 초기화되면 holding 참조가 더 이상 유효하지 않으니 이전 체결 결과는 지운다.
      onChange()
    } catch (err) {
      setFormError(err instanceof Error ? err.message : '초기화하지 못했어요.')
    } finally {
      setMutating(null)
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
          <button type="button" className="reset-button" onClick={() => void handleReset()} disabled={!!mutating}>
            {mutating === 'reset' ? '초기화 중…' : '초기화'}
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
          <button type="submit" disabled={!!mutating}>
            {mutating === 'order' ? '주문 중…' : '주문'}
          </button>
        </form>
        {preview && ticker.trim() && Number(quantity) > 0 && (
          <p className="muted small order-preview">
            예상 금액: {preview.companyName} {formatPrice(preview.price)}원 × {quantity}주 ={' '}
            {formatPrice(preview.price * Number(quantity))}원 (현재가 기준, 실제 체결가는 달라질 수 있어요)
          </p>
        )}
        {formError && <p className="notice notice-error">{formError}</p>}
        {lastOrder && (
          <p className="notice order-result">
            {lastOrder.side === 'buy' ? '매수' : '매도'} 체결: {lastOrder.company_name} {lastOrder.quantity}주 @{' '}
            {formatPrice(lastOrder.price)}원 (잔액 {formatPrice(lastOrder.cash_balance)}원
            {lastOrder.holding
              ? `, 보유 ${lastOrder.holding.quantity}주 · 평균 ${formatPrice(lastOrder.holding.avg_price)}원`
              : ' · 전량 매도로 보유 종목 없음'}
            )
          </p>
        )}

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

      {/* reloadKey가 바뀌면(주문/초기화) 통째로 리마운트해서 진단 결과를 지운다 - 주문 전에 받은 진단이
          그대로 남아있으면 바뀐 보유 종목과 안 맞는 설명을 보여주게 된다. */}
      <DiagnosisSection key={reloadKey} />
      <TradesSection reloadKey={reloadKey} />
      <WatchlistSection />
    </div>
  )
}
