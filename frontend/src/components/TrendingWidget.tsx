import { useState } from 'react'
import { api } from '../api'
import { useAsync } from '../hooks/useAsync'
import type { TrendingCategory } from '../types'
import { formatPct, formatPrice, trendClass } from '../utils/format'

const CATEGORY_LABELS: Record<TrendingCategory, string> = {
  gainers: '급등',
  losers: '급락',
  volume_surge: '거래량 급증',
}

const CATEGORIES: TrendingCategory[] = ['gainers', 'losers', 'volume_surge']

// 질문 없이 바로 보여주는 "오늘의 관심 종목"(GET /api/discovery/trending). 종목을 누르면
// 그 종목에 대한 질문을 입력창에 채워준다(바로 보내지는 않음).
export default function TrendingWidget({ onPick }: { onPick: (question: string) => void }) {
  const [category, setCategory] = useState<TrendingCategory>('gainers')
  const { data, error, loading } = useAsync(() => api.getTrending(category, 5), [category])

  return (
    <div className="trending card">
      <div className="card-head">
        <h2 className="section-title">오늘의 관심 종목</h2>
        <div className="segmented" role="group" aria-label="분류">
          {CATEGORIES.map((c) => (
            <button
              key={c}
              type="button"
              className={c === category ? 'seg active' : 'seg'}
              onClick={() => setCategory(c)}
            >
              {CATEGORY_LABELS[c]}
            </button>
          ))}
        </div>
      </div>

      {loading && !data && <p className="muted small">불러오는 중…</p>}
      {error && <p className="notice notice-error small">{error.message}</p>}
      {data && data.items.length === 0 && <p className="muted small">조건에 맞는 종목이 없어요.</p>}

      <ul className="trending-list">
        {data?.items.map((item) => (
          <li key={item.ticker}>
            <button
              type="button"
              onClick={() => onPick(`${item.company_name} 오늘 왜 이렇게 움직였어? 관련 뉴스도 같이 확인해줘.`)}
            >
              <span className="trending-name">{item.company_name}</span>
              <span className="meta meta-end">
                <span className="num">{formatPrice(item.close_price)}원</span>
                <span className={`num ${trendClass(item.change_pct)}`}>{formatPct(item.change_pct)}</span>
                {category === 'volume_surge' && item.volume_ratio != null && (
                  <span className="muted">{item.volume_ratio}배</span>
                )}
              </span>
            </button>
          </li>
        ))}
      </ul>
    </div>
  )
}
