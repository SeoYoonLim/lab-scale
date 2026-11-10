import { ApiError, classifyError, isRetryable } from '../../api/errors'
import { useLocale } from '../../i18n/useLocale'

// 서버가 준 문장(detail)은 한국어라 그대로 두고, 클라이언트가 직접 만든 오류(시간 초과·응답 형식 오류·본문 없는 실패·요청 한도 안내)와
// 네트워크 끊김(fetch의 TypeError)만 화면 언어로 바꿔 보여준다. 해당하지 않으면 null.
function localizedMessage(error: unknown, t: ReturnType<typeof useLocale>['t']): string | null {
  if (error instanceof ApiError) {
    if (error.clientCode === 'timeout') return t('errors.client_timeout', { fallback: error.message })
    if (error.clientCode === 'schema') return t('errors.client_schema', { fallback: error.message })
    if (error.clientCode === 'request_failed') {
      const base = t('errors.client_request_failed', { fallback: error.message, vars: { status: error.status } })
      return error.retryAfterSec != null ? `${base} ${t('errors.retry_after', { fallback: '', vars: { n: error.retryAfterSec } })}` : base
    }
    if (error.status === 429 && error.retryAfterSec != null && error.serverMessage) {
      return `${error.serverMessage} ${t('errors.retry_after', { fallback: '({n}초 뒤 다시 시도해주세요)', vars: { n: error.retryAfterSec } })}`
    }
    return null
  }
  // 브라우저가 서버에 닿지 못하면 fetch가 TypeError를 던진다("Failed to fetch" 등 브라우저 영어 문장)
  if (error instanceof TypeError) return t('errors.network')
  return null
}

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
  const message = localizedMessage(error, t) ?? (error instanceof Error && error.message ? error.message : (fallback ?? t('errors.500')))
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
