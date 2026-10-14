import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import { api } from '../../api'
import { queryKeys } from '../../api/queryKeys'
import AssetTypeBadge from '../../components/AssetTypeBadge'
import type { Portfolio } from '../../types'
import { formatPct, formatPrice, trendClass } from '../../utils/format'

const ORDER_PREVIEW_DEBOUNCE_MS = 400
const MAX_ORDER_QUANTITY = 100_000 // backend/API.md: 주문 수량은 1~100,000주(정수)

export default function HoldingsSection({ portfolio, onMutated }: { portfolio: Portfolio; onMutated: () => void }) {
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
