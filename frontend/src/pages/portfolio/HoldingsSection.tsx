import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import { api } from '../../api'
import { queryKeys } from '../../api/queryKeys'
import AssetTypeBadge from '../../components/AssetTypeBadge'
import { useLocale } from '../../i18n/useLocale'
import type { Portfolio } from '../../types'
import { formatPct, formatPrice, trendClass } from '../../utils/format'

const ORDER_PREVIEW_DEBOUNCE_MS = 400
const MAX_ORDER_QUANTITY = 100_000 // backend/API.md: 주문 수량은 1~100,000주(정수)

export default function HoldingsSection({ portfolio, onMutated }: { portfolio: Portfolio; onMutated?: () => void }) {
  const { t } = useLocale()
  const queryClient = useQueryClient()
  const won = t('unit.won', { fallback: '원' })
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
    mutationFn: ({ name, s, qty }: { name: string; s: 'buy' | 'sell'; qty: number }) => api.placeOrder(name, s, qty),
    onSuccess: () => {
      invalidatePortfolioQueries()
      onMutated?.()
    },
  })
  const resetMutation = useMutation({
    mutationFn: () => api.resetPortfolio(),
    onSuccess: () => {
      invalidatePortfolioQueries()
      onMutated?.()
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
      setFormError(
        t('holdings.qty_range', {
          fallback: '수량은 1~{max} 사이의 정수여야 해요.',
          vars: { max: MAX_ORDER_QUANTITY.toLocaleString('ko-KR') },
        }),
      )
      return
    }
    setFormError(null)
    try {
      await orderMutation.mutateAsync({ name: ticker.trim(), s: side, qty })
      setTicker('')
      setQuantity('1')
      setDebouncedTicker('')
    } catch (err) {
      setFormError(err instanceof Error ? err.message : t('holdings.order_failed', { fallback: '주문에 실패했어요.' }))
    }
  }

  async function handleReset() {
    if (mutating) return
    if (
      !confirm(
        t('holdings.reset_confirm', {
          fallback:
            '모의투자를 초기화할까요? 보유 종목과 체결 내역이 모두 삭제되고 잔고가 1,000만원으로 돌아가요. ' +
            '관심종목·리서치 기록은 그대로 남고, 되돌릴 수 없어요.',
        }),
      )
    ) {
      return
    }
    setFormError(null)
    try {
      await resetMutation.mutateAsync()
    } catch (err) {
      setFormError(err instanceof Error ? err.message : t('holdings.reset_failed', { fallback: '초기화하지 못했어요.' }))
    }
  }

  return (
    <section>
      <h2 className="section-title">{t('holdings.title', { fallback: '모의투자' })}</h2>
      <div className="card">
        <div className="portfolio-summary">
          <div>
            <div className="muted small">{t('exposure.cash', { fallback: '현금' })}</div>
            <div className="num price-big">
              {formatPrice(portfolio.cash_balance)}
              {won}
            </div>
          </div>
          <div>
            <div className="muted small">{t('holdings.eval', { fallback: '평가금액' })}</div>
            <div className="num price-big">
              {formatPrice(portfolio.total_eval_amount)}
              {won}
            </div>
          </div>
          <div>
            <div className="muted small">{t('holdings.total_asset', { fallback: '총 자산' })}</div>
            <div className="num price-big">
              {formatPrice(portfolio.total_asset)}
              {won}
            </div>
          </div>
        </div>

        <div className="portfolio-actions">
          <button type="button" className="reset-button" onClick={() => void handleReset()} disabled={!!mutating}>
            {mutating === 'reset' ? t('holdings.resetting', { fallback: '초기화 중…' }) : t('common.reset')}
          </button>
        </div>

        {portfolio.note && <p className="notice">{portfolio.note}</p>}

        <form className="order-form" onSubmit={handleOrder}>
          <input
            value={ticker}
            onChange={(e) => setTicker(e.target.value)}
            placeholder={t('holdings.ticker_placeholder', { fallback: '종목명 또는 종목코드' })}
            aria-label={t('holdings.ticker_aria', { fallback: '주문할 종목' })}
          />
          <select value={side} onChange={(e) => setSide(e.target.value as 'buy' | 'sell')} aria-label={t('holdings.side_aria', { fallback: '매수/매도' })}>
            <option value="buy">{t('holdings.buy', { fallback: '매수' })}</option>
            <option value="sell">{t('holdings.sell', { fallback: '매도' })}</option>
          </select>
          <input
            type="number"
            min={1}
            value={quantity}
            onChange={(e) => setQuantity(e.target.value)}
            aria-label={t('holdings.qty_aria', { fallback: '수량' })}
          />
          <button type="submit" disabled={!!mutating}>
            {mutating === 'order' ? t('holdings.ordering', { fallback: '주문 중…' }) : t('holdings.order', { fallback: '주문' })}
          </button>
        </form>
        {preview && ticker.trim() && Number(quantity) > 0 && (
          <p className="muted small order-preview">
            {t('holdings.preview', {
              fallback: '예상 금액: {name} {price} × {qty}주 = {total} (현재가 기준, 실제 체결가는 달라질 수 있어요)',
              vars: {
                name: preview.companyName,
                price: `${formatPrice(preview.price)}${won}`,
                qty: quantity,
                total: `${formatPrice(preview.price * Number(quantity))}${won}`,
              },
            })}
          </p>
        )}
        {formError && <p className="notice notice-error">{formError}</p>}
        {orderMutation.data && (
          <p className="notice order-result">
            {t('holdings.filled', {
              fallback: '{side} 체결: {name} {qty}주 @ {price} (잔액 {balance}{tail})',
              vars: {
                side:
                  orderMutation.data.side === 'buy'
                    ? t('holdings.buy', { fallback: '매수' })
                    : t('holdings.sell', { fallback: '매도' }),
                name: orderMutation.data.company_name,
                qty: orderMutation.data.quantity,
                price: `${formatPrice(orderMutation.data.price)}${won}`,
                balance: `${formatPrice(orderMutation.data.cash_balance)}${won}`,
                tail: orderMutation.data.holding
                  ? t('holdings.filled_holding', {
                      fallback: ', 보유 {qty}주 · 평균 {avg}',
                      vars: {
                        qty: orderMutation.data.holding.quantity,
                        avg: `${formatPrice(orderMutation.data.holding.avg_price)}${won}`,
                      },
                    })
                  : t('holdings.filled_none', { fallback: ' · 전량 매도로 보유 종목 없음' }),
              },
            })}
          </p>
        )}

        {portfolio.holdings.length === 0 ? (
          <p className="muted">{t('holdings.none', { fallback: '보유 종목이 없어요.' })}</p>
        ) : (
          <ul className="ledger">
            {portfolio.holdings.map((h) => (
              <li key={h.ticker} className="ledger-row watch-row">
                <div>
                  <div className="ledger-question">
                    {h.company_name} <AssetTypeBadge assetType={h.asset_type} />
                  </div>
                  <div className="meta muted small">
                    <span>{t('holdings.qty_shares', { fallback: '{n}주', vars: { n: h.quantity } })}</span>
                    <span>
                      {t('holdings.avg_price', { fallback: '평균 {price}', vars: { price: `${formatPrice(h.avg_price)}${won}` } })}
                    </span>
                  </div>
                </div>
                <div className="watch-right">
                  <div className="meta meta-end">
                    <span className="num">{h.current_price != null ? `${formatPrice(h.current_price)}${won}` : '-'}</span>
                    {/* 실시간 소스가 실패해 DB 최근 종가로 폴백된 가격은 조용히 숨기지 않고 표시한다
                        (CompanyPage의 실시간 시세 표시와 같은 패턴, backend/API.md의 holdings[].is_realtime). */}
                    {h.current_price != null && !h.is_realtime && (
                      <span className="muted small">{t('holdings.close_basis', { fallback: '종가 기준' })}</span>
                    )}
                    <span className={`num ${trendClass(h.profit_loss_pct)}`}>
                      {h.profit_loss != null ? `${formatPrice(h.profit_loss)}${won} (${formatPct(h.profit_loss_pct)})` : '-'}
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
