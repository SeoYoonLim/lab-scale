import type { AssetType } from '../types'

// 주식은 기본값이라 뱃지를 안 보여주고, ETF만 표시한다(backend/API.md "종목 유형: 주식과 ETF").
export default function AssetTypeBadge({ assetType }: { assetType: AssetType }) {
  if (assetType !== 'etf') return null
  return <span className="asset-type-badge">ETF</span>
}
