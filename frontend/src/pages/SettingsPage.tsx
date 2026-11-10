import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import type { FormEvent } from 'react'
import { api } from '../api'
import { LOCALES } from '../api/contracts/common'
import type { Locale } from '../api/contracts/common'
import { queryKeys } from '../api/queryKeys'
import ErrorNotice from '../components/ui/ErrorNotice'
import { LOCALE_NATIVE_NAMES } from '../i18n'
import { useLocale } from '../i18n/useLocale'

// 브라우저가 아는 IANA 시간대 목록(자동완성용). 목록에 없는 이름은 서버가 422로 막는다.
function knownTimezones(): string[] {
  try {
    return Intl.supportedValuesOf('timeZone')
  } catch {
    return ['Asia/Seoul', 'Asia/Tokyo', 'America/New_York', 'Europe/London', 'UTC']
  }
}

// 언어·시간대 설정(GET/PUT /api/auth/preferences). 저장하면 AI 답변·브리핑·성과 문장의 언어와 기록의 날짜 묶음 기준이 바뀐다.
// 이미 저장된 기록은 작성 당시 언어 그대로고(다시 번역하지 않는다), 언어 선택 목록은 각 언어의 자기 이름으로 보여준다.
export default function SettingsPage() {
  const { t, preferences } = useLocale()
  const queryClient = useQueryClient()
  const query = useQuery({ queryKey: queryKeys.preferences, queryFn: () => api.getPreferences(), staleTime: Infinity })
  const [localeDraft, setLocaleDraft] = useState<Locale | null>(null)
  const [timezoneDraft, setTimezoneDraft] = useState<string | null>(null)

  const current = query.data ?? preferences
  const locale = localeDraft ?? (current?.preferred_locale as Locale | undefined) ?? 'ko-KR'
  const timezone = timezoneDraft ?? current?.preferred_timezone ?? ''

  const save = useMutation({
    mutationFn: () =>
      api.updatePreferences({
        preferred_locale: locale,
        // 비우면 null을 보내 기본(Asia/Seoul)으로 돌린다
        preferred_timezone: timezone.trim() === '' ? null : timezone.trim(),
      }),
    onSuccess: (saved) => {
      queryClient.setQueryData(queryKeys.preferences, saved)
      setLocaleDraft(null)
      setTimezoneDraft(null)
      // 서버가 문장을 그 언어로 만들어 주는 응답은 캐시를 비워 새 언어로 다시 받는다
      void queryClient.invalidateQueries({ predicate: (q) => q.queryKey[0] !== 'preferences' })
    },
  })

  function handleSubmit(e: FormEvent) {
    e.preventDefault()
    save.mutate()
  }

  const dirty =
    current != null &&
    (locale !== current.preferred_locale || (timezone.trim() === '' ? null : timezone.trim()) !== current.preferred_timezone)

  return (
    <div className="container page settings-page">
      <h1 className="page-title">{t('settings.title')}</h1>
      <form className="card settings-card" onSubmit={handleSubmit}>
        {query.isPending && <p className="muted">{t('common.loading')}</p>}
        {query.error && <ErrorNotice error={query.error} onRetry={() => void query.refetch()} />}

        <label className="auth-field">
          <span>{t('settings.language')}</span>
          <select value={locale} onChange={(e) => setLocaleDraft(e.target.value as Locale)}>
            {LOCALES.map((code) => (
              <option key={code} value={code}>
                {LOCALE_NATIVE_NAMES[code]}
              </option>
            ))}
          </select>
          <span className="muted small">{t('settings.language_help')}</span>
        </label>

        <label className="auth-field">
          <span>{t('settings.timezone')}</span>
          <input
            list="timezone-options"
            value={timezone}
            onChange={(e) => setTimezoneDraft(e.target.value)}
            placeholder={t('settings.timezone_placeholder', { fallback: '예: Asia/Seoul' })}
          />
          <datalist id="timezone-options">
            {knownTimezones().map((name) => (
              <option key={name} value={name} />
            ))}
          </datalist>
          <span className="muted small">
            {t('settings.timezone_help')}
            {current && <> ({current.effective_timezone})</>}
          </span>
        </label>

        {save.error && <ErrorNotice error={save.error} fallback={t('settings.save_failed')} />}
        {save.isSuccess && !dirty && <p className="notice order-result" role="status">{t('settings.saved')}</p>}

        <button type="submit" className="send" disabled={save.isPending || !dirty}>
          {save.isPending ? t('common.loading') : t('common.save')}
        </button>
      </form>
    </div>
  )
}
