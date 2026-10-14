import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { api } from '../api'
import { queryKeys } from '../api/queryKeys'
import DiagnosisHistorySection from './portfolio/DiagnosisHistorySection'
import DiagnosisSection from './portfolio/DiagnosisSection'
import HoldingsSection from './portfolio/HoldingsSection'
import TradesSection from './portfolio/TradesSection'
import WatchlistSection from './portfolio/WatchlistSection'

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
