import { useQuery } from '@tanstack/react-query'
import { api } from '../../api'
import { queryKeys } from '../../api/queryKeys'
import { useLocale } from '../../i18n/useLocale'
import type { Holding } from '../../types'

const WATCHLIST_PICK_LIMIT = 8
const TRENDING_PICK_LIMIT = 5

interface Pick {
  ticker: string
  name: string
  // 보유 종목이면 보유 수량(칩에 "N주"로 같이 보인다)
  heldQuantity?: number
}

// 주문 칸 아래의 "빠른 선택". 종목 이름·코드를 직접 치는 대신 누르면 주문 종목 칸이 채워진다.
//   매수: 내 관심종목, 오늘의 관심 종목(급등) 중에서 고른다
//   매도: 가진 종목만 보여주고(오타가 날 일이 없다), 고른 종목은 "전량 매도"로 수량을 한 번에 채운다
// 칩은 토글 버튼(aria-pressed)이라 지금 고른 종목이 소리로도 알려진다.
export default function OrderQuickPicks({
  side,
  ticker,
  holdings,
  onPick,
  onSellAll,
}: {
  side: 'buy' | 'sell'
  // 주문 종목 칸에 지금 들어 있는 값(이름 또는 코드)
  ticker: string
  holdings: Holding[]
  onPick: (pick: Pick) => void
  onSellAll: (holding: Holding) => void
}) {
  const { t } = useLocale()
  const buying = side === 'buy'

  // 매도 중에는 쓰지 않으니 요청도 보내지 않는다
  const watchlist = useQuery({ queryKey: queryKeys.watchlist, queryFn: () => api.listWatchlist(), enabled: buying })
  const trending = useQuery({
    queryKey: queryKeys.trending('gainers', 'stock'),
    queryFn: () => api.getTrending('gainers', TRENDING_PICK_LIMIT, 'stock'),
    enabled: buying,
  })

  const current = ticker.trim()
  const isSelected = (pick: Pick) => current !== '' && (current === pick.ticker || current === pick.name)

  function renderChips(label: string, picks: Pick[]) {
    if (picks.length === 0) return null
    return (
      <div className="order-picks-group" role="group" aria-label={label}>
        <span className="order-picks-label muted small">{label}</span>
        <div className="order-picks-chips">
          {picks.map((pick) => (
            <button
              key={pick.ticker}
              type="button"
              className={isSelected(pick) ? 'pick-chip active' : 'pick-chip'}
              aria-pressed={isSelected(pick)}
              onClick={() => onPick(pick)}
            >
              <span>{pick.name}</span>
              {pick.heldQuantity != null && (
                <span className="pick-chip-qty num">{t('holdings.qty_shares', { fallback: '{n}주', vars: { n: pick.heldQuantity } })}</span>
              )}
            </button>
          ))}
        </div>
      </div>
    )
  }

  if (!buying) {
    const heldPicks: Pick[] = holdings.map((h) => ({ ticker: h.ticker, name: h.company_name, heldQuantity: h.quantity }))
    const selected = holdings.find((h) => current !== '' && (current === h.ticker || current === h.company_name))
    return (
      <div className="order-picks">
        {heldPicks.length === 0 ? (
          <p className="muted small">{t('holdings.nothing_to_sell', { fallback: '아직 팔 수 있는 보유 종목이 없어요.' })}</p>
        ) : (
          renderChips(t('holdings.pick_holdings', { fallback: '보유 종목' }), heldPicks)
        )}
        {selected && (
          <div className="order-picks-sellall">
            <span className="muted small">{t('holdings.held_qty', { fallback: '{name} {n}주를 가지고 있어요', vars: { name: selected.company_name, n: selected.quantity } })}</span>
            <button type="button" className="link-button" onClick={() => onSellAll(selected)}>
              {t('holdings.sell_all', { fallback: '전량 매도({n}주)', vars: { n: selected.quantity } })}
            </button>
          </div>
        )}
      </div>
    )
  }

  const watchPicks: Pick[] = (watchlist.data ?? []).slice(0, WATCHLIST_PICK_LIMIT).map((w) => ({ ticker: w.ticker, name: w.company_name }))
  const trendPicks: Pick[] = (trending.data?.items ?? []).map((i) => ({ ticker: i.ticker, name: i.company_name }))
  if (watchPicks.length === 0 && trendPicks.length === 0) return null
  return (
    <div className="order-picks">
      {renderChips(t('holdings.pick_watchlist', { fallback: '관심종목' }), watchPicks)}
      {renderChips(t('holdings.pick_trending', { fallback: '오늘의 관심 종목' }), trendPicks)}
    </div>
  )
}
