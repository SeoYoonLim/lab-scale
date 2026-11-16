import { useQuery } from '@tanstack/react-query'
import { api } from '../../api'
import { queryKeys } from '../../api/queryKeys'
import ErrorNotice from '../../components/ui/ErrorNotice'
import HoldingsSection from './HoldingsSection'
import LoadingNotice from '../../components/ui/LoadingNotice'

export default function HoldingsTab() {
  const { data, error, isPending, refetch } = useQuery({ queryKey: queryKeys.portfolio, queryFn: () => api.getPortfolio() })

  return (
    <>
      {isPending && <LoadingNotice />}
      {error && <ErrorNotice error={error} onRetry={() => void refetch()} />}
      {data && <HoldingsSection portfolio={data} />}
    </>
  )
}
