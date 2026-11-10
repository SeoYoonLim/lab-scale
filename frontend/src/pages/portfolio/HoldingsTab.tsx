import { useQuery } from '@tanstack/react-query'
import { api } from '../../api'
import { queryKeys } from '../../api/queryKeys'
import ErrorNotice from '../../components/ui/ErrorNotice'
import { useLocale } from '../../i18n/useLocale'
import HoldingsSection from './HoldingsSection'

export default function HoldingsTab() {
  const { t } = useLocale()
  const { data, error, isPending, refetch } = useQuery({ queryKey: queryKeys.portfolio, queryFn: () => api.getPortfolio() })

  return (
    <>
      {isPending && <p className="muted">{t('common.loading')}</p>}
      {error && <ErrorNotice error={error} onRetry={() => void refetch()} />}
      {data && <HoldingsSection portfolio={data} />}
    </>
  )
}
