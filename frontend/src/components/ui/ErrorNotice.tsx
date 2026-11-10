import { classifyError, isRetryable } from '../../api/errors'
import { useLocale } from '../../i18n/useLocale'

// API 오류를 화면마다 같은 모양으로 보여준다(핸드오프 6절). 서버가 준 detail(한국어 문장)이 가장 정확해서 그대로 쓰고,
// 다시 시도하면 될 수 있는 오류(서버·시간 초과·한도)에만 재시도 버튼을 붙인다. 401은 AuthContext가 로그인 화면으로 보낸다.
export default function ErrorNotice({
  error,
  onRetry,
  fallback,
}: {
  error: unknown
  onRetry?: () => void
  fallback?: string
}) {
  const { t } = useLocale()
  const kind = classifyError(error)
  const message = error instanceof Error && error.message ? error.message : (fallback ?? t('errors.500'))
  const retryable = onRetry != null && isRetryable(error)

  return (
    <div className="notice notice-error error-notice" role="alert" data-error-kind={kind}>
      <span>{message}</span>
      {retryable && (
        <button type="button" className="error-retry" onClick={onRetry}>
          {t('common.retry')}
        </button>
      )}
    </div>
  )
}
