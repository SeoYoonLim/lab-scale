import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { api } from '../api'
import { queryKeys } from '../api/queryKeys'
import { useLocale } from '../i18n/useLocale'
import type { AssetType, TrendingCategory } from '../types'
import { formatPct, formatPrice, trendClass } from '../utils/format'
import ErrorNotice from './ui/ErrorNotice'

const CATEGORY_LABELS: Record<TrendingCategory, { key: string; fallback: string }> = {
  gainers: { key: 'trending.gainers', fallback: '급등' },
  losers: { key: 'trending.losers', fallback: '급락' },
  volume_surge: { key: 'trending.volume_surge', fallback: '거래량 급증' },
}

const CATEGORIES: TrendingCategory[] = ['gainers', 'losers', 'volume_surge']

const ASSET_TYPE_LABELS: Record<AssetType, { key: string; fallback: string }> = {
  stock: { key: 'asset.stock', fallback: '주식' },
  etf: { key: 'asset.etf', fallback: 'ETF' },
}
const ASSET_TYPES: AssetType[] = ['stock', 'etf']

// 질문 없이 바로 보여주는 "오늘의 관심 종목"(GET /api/discovery/trending). 종목을 누르면
// 그 종목에 대한 질문을 입력창에 채워준다(바로 보내지는 않음).
export default function TrendingWidget({ onPick }: { onPick: (question: string) => void }) {
  const { t } = useLocale()
  const [category, setCategory] = useState<TrendingCategory>('gainers')
  // 기본은 주식만(backend/API.md) - ETF도 따로 볼 수 있게 토글을 둔다.
  const [assetType, setAssetType] = useState<AssetType>('stock')
  const { data, error, isPending, refetch } = useQuery({
    queryKey: queryKeys.trending(category, assetType),
    queryFn: () => api.getTrending(category, 5, assetType),
  })

  return (
    <div className="trending card">
      <div className="card-head">
        <h2 className="section-title">{t('trending.title', { fallback: '오늘의 관심 종목' })}</h2>
        <div className="trending-filters">
          <div className="segmented" role="group" aria-label={t('trending.category_aria', { fallback: '분류' })}>
            {CATEGORIES.map((c) => (
              <button
                key={c}
                type="button"
                className={c === category ? 'seg active' : 'seg'}
                onClick={() => setCategory(c)}
              >
                {t(CATEGORY_LABELS[c].key, { fallback: CATEGORY_LABELS[c].fallback })}
              </button>
            ))}
          </div>
          <div className="segmented" role="group" aria-label={t('trending.type_aria', { fallback: '종목 유형' })}>
            {ASSET_TYPES.map((type) => (
              <button
                key={type}
                type="button"
                className={type === assetType ? 'seg active' : 'seg'}
                onClick={() => setAssetType(type)}
              >
                {t(ASSET_TYPE_LABELS[type].key, { fallback: ASSET_TYPE_LABELS[type].fallback })}
              </button>
            ))}
          </div>
        </div>
      </div>

      {isPending && <p className="muted small">{t('common.loading')}</p>}
      {error && <ErrorNotice error={error} onRetry={() => void refetch()} />}
      {data && data.items.length === 0 && <p className="muted small">{t('trending.empty', { fallback: '조건에 맞는 종목이 없어요.' })}</p>}

      <ul className="trending-list">
        {data?.items.map((item) => (
          <li key={item.ticker}>
            <button
              type="button"
              // 이 문장은 백엔드 리서치에 보내는 질문이다. 백엔드가 한국어 키워드로 질문 유형을 나누므로 번역하지 않는다.
              onClick={() => onPick(`${item.company_name} 오늘 왜 이렇게 움직였어? 관련 뉴스도 같이 확인해줘.`)}
            >
              <span className="trending-name">{item.company_name}</span>
              <span className="meta meta-end">
                <span className="num">
                  {formatPrice(item.close_price)}
                  {t('unit.won', { fallback: '원' })}
                </span>
                <span className={`num ${trendClass(item.change_pct)}`}>{formatPct(item.change_pct)}</span>
                {category === 'volume_surge' && item.volume_ratio != null && (
                  <span className="muted">
                    {item.volume_ratio}
                    {t('common.unit_times')}
                  </span>
                )}
              </span>
            </button>
          </li>
        ))}
      </ul>
    </div>
  )
}
