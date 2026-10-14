import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import { api } from '../api'
import { ApiError } from '../api/errors'
import { queryKeys } from '../api/queryKeys'
import AssetTypeBadge from '../components/AssetTypeBadge'
import DiagnosisCompareView from '../components/DiagnosisCompareView'
import DiagnosisResultView from '../components/DiagnosisResultView'
import type { Portfolio, WatchlistItem } from '../types'
import { formatDate, formatPct, formatPrice, trendClass } from '../utils/format'

const ORDER_PREVIEW_DEBOUNCE_MS = 400
const TRADES_PAGE_SIZE = 20
const MAX_ORDER_QUANTITY = 100_000 // backend/API.md: 주문 수량은 1~100,000주(정수)

function WatchlistSection() {
  const queryClient = useQueryClient()
  const { data, error, isPending } = useQuery({ queryKey: queryKeys.watchlist, queryFn: () => api.listWatchlist() })
  const [ticker, setTicker] = useState('')
  const [formError, setFormError] = useState<string | null>(null)
  const [formNotice, setFormNotice] = useState<string | null>(null)

  const addMutation = useMutation({
    mutationFn: (t: string) => api.addWatchlistItem(t),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: queryKeys.watchlist }),
  })
  const removeMutation = useMutation({
    mutationFn: (t: string) => api.removeWatchlistItem(t),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: queryKeys.watchlist }),
  })

  async function handleAdd(e: FormEvent) {
    e.preventDefault()
    if (!ticker.trim() || addMutation.isPending) return
    setFormError(null)
    setFormNotice(null)
    try {
      await addMutation.mutateAsync(ticker.trim())
      setTicker('')
    } catch (err) {
      // CompanyPage의 "이미 관심종목이에요"와 똑같이, 이미 등록된 종목(409)은 에러가 아니라
      // 안내로 보여준다 - 메시지 자체는 서버/mock이 주는 문구를 그대로 쓴다.
      if (err instanceof ApiError && err.status === 409) {
        setFormNotice(err.message)
      } else {
        setFormError(err instanceof Error ? err.message : '추가하지 못했어요.')
      }
    }
  }

  async function handleRemove(item: WatchlistItem) {
    try {
      await removeMutation.mutateAsync(item.ticker)
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
          <button type="submit" disabled={addMutation.isPending || !ticker.trim()}>
            추가
          </button>
        </form>
        {formError && <p className="notice notice-error">{formError}</p>}
        {formNotice && <p className="muted small">{formNotice}</p>}

        {isPending && <p className="muted">불러오는 중…</p>}
        {error && <p className="notice notice-error">{error.message}</p>}
        {data && data.length === 0 && <p className="muted">아직 등록한 관심종목이 없어요.</p>}

        <ul className="ledger">
          {data?.map((item) => (
            <li key={item.ticker} className="ledger-row watch-row">
              <div>
                <div className="ledger-question">
                  {item.company_name} <AssetTypeBadge assetType={item.asset_type} />
                </div>
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

function HoldingsSection({ portfolio, onMutated }: { portfolio: Portfolio; onMutated: () => void }) {
  const queryClient = useQueryClient()
  const [ticker, setTicker] = useState('')
  const [side, setSide] = useState<'buy' | 'sell'>('buy')
  const [quantity, setQuantity] = useState('1')
  const [formError, setFormError] = useState<string | null>(null)
  const [debouncedTicker, setDebouncedTicker] = useState('')

  function invalidatePortfolioQueries() {
    void queryClient.invalidateQueries({ queryKey: queryKeys.portfolio })
    // 부분 키라서 오프셋·필터가 다른 모든 trades 쿼리가 한꺼번에 무효화된다.
    void queryClient.invalidateQueries({ queryKey: ['trades'] })
  }

  const orderMutation = useMutation({
    mutationFn: ({ t, s, qty }: { t: string; s: 'buy' | 'sell'; qty: number }) => api.placeOrder(t, s, qty),
    onSuccess: () => {
      invalidatePortfolioQueries()
      onMutated()
    },
  })
  const resetMutation = useMutation({
    mutationFn: () => api.resetPortfolio(),
    onSuccess: () => {
      invalidatePortfolioQueries()
      onMutated()
      // 초기화되면 holding 참조가 더 이상 유효하지 않으니, 이전 주문의 체결 결과 안내도 지운다 -
      // orderMutation.data는 초기화와 별개라서 그냥 두면 리셋 후에도 옛 체결 결과가 그대로 남는다.
      orderMutation.reset()
    },
  })
  // 주문과 초기화가 동시에 들어가면 보유 종목이 꼬일 수 있어서(초기화 중에 주문이 체결되는 등),
  // 둘 중 하나가 진행 중이면 다른 쪽도 같이 막는다.
  const mutating = orderMutation.isPending ? 'order' : resetMutation.isPending ? 'reset' : null

  // 입력한 종목의 실시간 시세로 "예상 금액"을 미리 보여준다(디바운스 400ms) - 실제 체결가는 주문
  // 시점 시세라 다를 수 있다. enabled가 false면 요청이 안 나가고, 실패(모르는 종목 등)는 error만
  // 세팅될 뿐 조용히 무시된다(타이핑 중일 수 있으니 여기서 에러로 보여주지 않는다 - 실제 검증은
  // 제출 시 placeOrder가 한다).
  const { data: previewPrice } = useQuery({
    queryKey: queryKeys.realtimePrice(debouncedTicker),
    queryFn: () => api.getRealtimePrice(debouncedTicker),
    enabled: debouncedTicker.length > 0,
    retry: false,
    staleTime: 3000,
  })
  const preview =
    previewPrice && debouncedTicker
      ? { companyName: previewPrice.company_name, price: previewPrice.current_price }
      : null

  useEffect(() => {
    const id = setTimeout(() => setDebouncedTicker(ticker.trim()), ORDER_PREVIEW_DEBOUNCE_MS)
    return () => clearTimeout(id)
  }, [ticker])

  async function handleOrder(e: FormEvent) {
    e.preventDefault()
    const qty = Number(quantity)
    if (!ticker.trim() || mutating) return
    if (!Number.isInteger(qty) || qty < 1 || qty > MAX_ORDER_QUANTITY) {
      setFormError(`수량은 1~${MAX_ORDER_QUANTITY.toLocaleString('ko-KR')} 사이의 정수여야 해요.`)
      return
    }
    setFormError(null)
    try {
      await orderMutation.mutateAsync({ t: ticker.trim(), s: side, qty })
      setTicker('')
      setQuantity('1')
      setDebouncedTicker('')
    } catch (err) {
      setFormError(err instanceof Error ? err.message : '주문에 실패했어요.')
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
    setFormError(null)
    try {
      await resetMutation.mutateAsync()
    } catch (err) {
      setFormError(err instanceof Error ? err.message : '초기화하지 못했어요.')
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
        {orderMutation.data && (
          <p className="notice order-result">
            {orderMutation.data.side === 'buy' ? '매수' : '매도'} 체결: {orderMutation.data.company_name}{' '}
            {orderMutation.data.quantity}주 @ {formatPrice(orderMutation.data.price)}원 (잔액{' '}
            {formatPrice(orderMutation.data.cash_balance)}원
            {orderMutation.data.holding
              ? `, 보유 ${orderMutation.data.holding.quantity}주 · 평균 ${formatPrice(orderMutation.data.holding.avg_price)}원`
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
                  <div className="ledger-question">
                    {h.company_name} <AssetTypeBadge assetType={h.asset_type} />
                  </div>
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

// 진단은 "항상 새로 계산"이고 결과를 캐시해서 재사용할 이유가 없다(backend/API.md) - 쿼리가 아니라
// 뮤테이션(버튼을 누르는 행동)으로 다룬다. 보유 종목이 바뀌면 부모가 key를 바꿔 이 컴포넌트를
// 통째로 리마운트시켜서 지난 결과를 지운다.
function DiagnosisSection() {
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

const DIAGNOSIS_HISTORY_PAGE_SIZE = 10

function DiagnosisHistorySection() {
  const queryClient = useQueryClient()
  const [offset, setOffset] = useState(0)
  const [expanded, setExpanded] = useState<{ id: number; view: 'detail' | 'compare' } | null>(null)

  const { data, error, isPending } = useQuery({
    queryKey: queryKeys.diagnosisHistory(DIAGNOSIS_HISTORY_PAGE_SIZE, offset),
    queryFn: () => api.getDiagnosisHistory(DIAGNOSIS_HISTORY_PAGE_SIZE, offset),
  })

  const detailQuery = useQuery({
    queryKey: queryKeys.diagnosisDetail(expanded?.id ?? -1),
    queryFn: () => api.getDiagnosisDetail(expanded!.id),
    enabled: expanded?.view === 'detail',
  })
  const compareQuery = useQuery({
    queryKey: queryKeys.diagnosisCompare(expanded?.id ?? -1),
    queryFn: () => api.compareDiagnosis(expanded!.id),
    enabled: expanded?.view === 'compare',
  })

  const deleteMutation = useMutation({
    mutationFn: (id: number) => api.deleteDiagnosis(id),
    onSuccess: () => {
      setExpanded(null)
      void queryClient.invalidateQueries({ queryKey: ['diagnosisHistory'] })
    },
  })

  function toggle(id: number, view: 'detail' | 'compare') {
    setExpanded((prev) => (prev?.id === id && prev.view === view ? null : { id, view }))
  }

  async function handleDelete(id: number) {
    if (!confirm('이 진단을 삭제할까요? 되돌릴 수 없어요.')) return
    await deleteMutation.mutateAsync(id)
  }

  return (
    <section>
      <h2 className="section-title">진단 이력</h2>
      <div className="card">
        {isPending && <p className="muted">불러오는 중…</p>}
        {error && <p className="notice notice-error">{error.message}</p>}
        {data && data.items.length === 0 && (
          <p className="muted">아직 저장된 진단이 없어요. 위에서 AI 진단을 받아보세요.</p>
        )}

        {data && data.items.length > 0 && (
          <ul className="ledger">
            {data.items.map((item) => (
              <li key={item.id} className="ledger-row">
                <div className="ledger-summary diagnosis-history-row">
                  <div>
                    <div className="ledger-head">
                      <span className="num">{formatDate(item.created_at)}</span>
                      <span>{item.source === 'llm' ? 'AI 설명' : '규칙 기반'}</span>
                    </div>
                    <div className="meta muted small">
                      <span className="num">{formatPrice(item.total_asset)}원</span>
                      <span>{item.holdings_count}종목</span>
                      {item.flags.length > 0 && <span>플래그 {item.flags.length}개</span>}
                    </div>
                  </div>
                  <div className="watch-right diagnosis-history-actions">
                    <button type="button" className="diagnosis-history-toggle" onClick={() => toggle(item.id, 'detail')}>
                      {expanded?.id === item.id && expanded.view === 'detail' ? '상세 닫기' : '상세'}
                    </button>
                    <button type="button" className="diagnosis-history-toggle" onClick={() => toggle(item.id, 'compare')}>
                      {expanded?.id === item.id && expanded.view === 'compare' ? '비교 닫기' : '이전과 비교'}
                    </button>
                    <button
                      type="button"
                      className="delete-report"
                      onClick={() => void handleDelete(item.id)}
                      disabled={deleteMutation.isPending}
                    >
                      삭제
                    </button>
                  </div>
                </div>

                {expanded?.id === item.id && expanded.view === 'detail' && (
                  <div className="ledger-detail">
                    {detailQuery.isPending && <p className="muted">불러오는 중…</p>}
                    {detailQuery.error && <p className="notice notice-error">{detailQuery.error.message}</p>}
                    {detailQuery.data && <DiagnosisResultView result={detailQuery.data} />}
                  </div>
                )}
                {expanded?.id === item.id && expanded.view === 'compare' && (
                  <div className="ledger-detail">
                    {compareQuery.isPending && <p className="muted">비교하는 중…</p>}
                    {compareQuery.error && <p className="notice notice-error">{compareQuery.error.message}</p>}
                    {compareQuery.data && <DiagnosisCompareView compare={compareQuery.data} />}
                  </div>
                )}
              </li>
            ))}
          </ul>
        )}

        {data && data.total > 0 && (
          <div className="pagination">
            <button
              type="button"
              disabled={offset === 0}
              onClick={() => setOffset(Math.max(0, offset - DIAGNOSIS_HISTORY_PAGE_SIZE))}
            >
              이전
            </button>
            <span className="muted small">
              {offset + 1}–{Math.min(offset + DIAGNOSIS_HISTORY_PAGE_SIZE, data.total)} / {data.total}
            </span>
            <button
              type="button"
              disabled={offset + DIAGNOSIS_HISTORY_PAGE_SIZE >= data.total}
              onClick={() => setOffset(offset + DIAGNOSIS_HISTORY_PAGE_SIZE)}
            >
              다음
            </button>
          </div>
        )}
      </div>
    </section>
  )
}

function TradesSection() {
  const [offset, setOffset] = useState(0)
  const [tickerFilter, setTickerFilter] = useState('')
  const [appliedFilter, setAppliedFilter] = useState('')
  // placeOrder/resetPortfolio 뮤테이션이 ['trades']를 무효화하면(쿼리 키 접두어 일치) 이 쿼리도
  // 자동으로 다시 불러온다 - reloadKey를 prop으로 내려받지 않아도 된다.
  const { data, error, isPending } = useQuery({
    queryKey: queryKeys.trades(TRADES_PAGE_SIZE, offset, appliedFilter || undefined),
    queryFn: () => api.getTrades(TRADES_PAGE_SIZE, offset, appliedFilter || undefined),
    placeholderData: keepPreviousData,
  })

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

        {isPending && <p className="muted">불러오는 중…</p>}
        {error && <p className="notice notice-error">{error.message}</p>}
        {data && data.items.length === 0 && <p className="muted">체결 내역이 없어요.</p>}

        {data && data.items.length > 0 && (
          <ul className="ledger">
            {data.items.map((t) => (
              <li key={t.id} className="ledger-row watch-row">
                <div>
                  <div className="ledger-question">
                    {t.company_name} <AssetTypeBadge assetType={t.asset_type} />
                  </div>
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
  const [resetSignal, setResetSignal] = useState(0)
  const { data, error, isPending } = useQuery({ queryKey: queryKeys.portfolio, queryFn: () => api.getPortfolio() })

  return (
    <div className="container page portfolio-page">
      <h1 className="page-title">포트폴리오</h1>

      {isPending && <p className="muted">불러오는 중…</p>}
      {error && <p className="notice notice-error">{error.message}</p>}
      {data && <HoldingsSection portfolio={data} onMutated={() => setResetSignal((k) => k + 1)} />}

      <DiagnosisSection key={resetSignal} />
      <DiagnosisHistorySection />
      <TradesSection />
      <WatchlistSection />
    </div>
  )
}
