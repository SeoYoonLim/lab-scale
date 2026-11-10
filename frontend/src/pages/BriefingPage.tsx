import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { api } from '../api'
import { ApiError } from '../api/errors'
import { groupHeaderIndexes } from '../api/contracts/history'
import type { DailyBriefing } from '../api/contracts/briefing'
import { queryKeys } from '../api/queryKeys'
import BriefingView from '../components/briefing/BriefingView'
import Pagination from '../components/Pagination'
import Banner from '../components/ui/Banner'
import EmptyState from '../components/ui/EmptyState'
import ErrorNotice from '../components/ui/ErrorNotice'
import StatusBadge from '../components/ui/StatusBadge'
import { languageName } from '../i18n'
import { toLocale } from '../i18n'
import { useLocale } from '../i18n/useLocale'
import { formatDateTime, formatTradingDate } from '../utils/format'

type Tab = 'today' | 'history'
const HISTORY_PAGE_SIZE = 10

// 브리핑 한 건(오늘 것이든 과거 저장본이든)의 머리말 + 본문. 과거 저장본은 만든 날 기준 스냅샷이라 그 뒤 시세는 반영되지 않았다는
// 안내를 붙이고, 한국어가 아닌 브리핑에는 같은 계산의 한국어 원문 토글이 있다(핸드오프 8절).
function BriefingDetail({ briefing, isPast, actions }: { briefing: DailyBriefing; isPast: boolean; actions?: React.ReactNode }) {
  const { t, locale, timezone } = useLocale()
  const [showOriginal, setShowOriginal] = useState(false)
  const content = showOriginal && briefing.original_content ? briefing.original_content : briefing.content

  return (
    <div className="card">
      <div className="briefing-head">
        <div>
          <h2 className="briefing-title">{content.title}</h2>
          <p className="muted small briefing-meta">
            <span>
              {t('daily_briefing.generated_at')} {formatDateTime(briefing.generated_at, timezone, locale)}
            </span>
            <span>
              {t('daily_briefing.data_as_of')} <span className="num">{formatTradingDate(briefing.data_as_of)}</span>
            </span>
            <StatusBadge>{t('daily_briefing.version', { vars: { n: briefing.version } })}</StatusBadge>
            {briefing.locale !== locale && <StatusBadge tone="accent">{t('common.written_in', { vars: { language: languageName(toLocale(briefing.locale), locale) } })}</StatusBadge>}
          </p>
        </div>
        <div className="briefing-actions">
          {briefing.original_content && (
            <button type="button" className="reset-button" aria-pressed={showOriginal} onClick={() => setShowOriginal((v) => !v)}>
              {t('daily_briefing.original_toggle')}
            </button>
          )}
          {actions}
        </div>
      </div>
      {isPast && (
        <Banner tone="info">
          {t('briefing.created_note', { fallback: '{date}에 만든 브리핑이에요. 그 뒤 시세는 반영되지 않았어요.', vars: { date: briefing.local_date } })}
        </Banner>
      )}
      <BriefingView content={content} />
    </div>
  )
}

function TodayTab({ onShowHistory }: { onShowHistory: () => void }) {
  const { t, timezone } = useLocale()
  const queryClient = useQueryClient()
  const [useLlm, setUseLlm] = useState(true)

  const current = useQuery({
    queryKey: queryKeys.dailyBriefingCurrent(timezone),
    queryFn: () => api.getCurrentDailyBriefing(timezone),
  })

  const generate = useMutation({
    mutationFn: (regenerate: boolean) => api.generateDailyBriefing({ regenerate, use_llm: useLlm }),
    onSuccess: (briefing) => {
      queryClient.setQueryData(queryKeys.dailyBriefingCurrent(timezone), briefing)
      void queryClient.invalidateQueries({ queryKey: ['dailyBriefing', 'history'] })
    },
  })

  const busy = generate.isPending
  const briefing = current.data
  const limitReached = generate.error instanceof ApiError && generate.error.status === 409

  const generateControls = (regenerate: boolean) => (
    <div className="briefing-generate">
      <label className="check-label">
        <input type="checkbox" checked={!useLlm} onChange={(e) => setUseLlm(!e.target.checked)} disabled={busy} />
        <span>{t('briefing.no_use_llm', { fallback: '빠르게(AI 요약 없이)' })}</span>
      </label>
      <button
        type="button"
        className="send"
        disabled={busy}
        onClick={() => {
          if (regenerate && !confirm(t('briefing.regenerate_confirm', { fallback: '오늘 브리핑의 새 버전을 만들까요?' }))) return
          generate.mutate(regenerate)
        }}
      >
        {busy ? t('daily_briefing.generating') : regenerate ? t('daily_briefing.regenerate') : t('daily_briefing.generate')}
      </button>
    </div>
  )

  return (
    <>
      {current.isPending && <p className="muted">{t('common.loading')}</p>}
      {current.error && <ErrorNotice error={current.error} onRetry={() => void current.refetch()} />}
      {generate.error && (
        <>
          {/* 409(하루 한도)는 오류가 아니라 안내 + 지난 버전으로 보내는 상태다 */}
          {limitReached ? (
            <Banner tone="warn">
              {t('daily_briefing.version_limit')}{' '}
              <button type="button" className="link-button" onClick={onShowHistory}>
                {t('daily_briefing.history_title')}
              </button>
            </Banner>
          ) : (
            <ErrorNotice error={generate.error} />
          )}
        </>
      )}

      {current.data === null && !current.isPending && (
        <div className="card">
          <EmptyState message={t('daily_briefing.not_generated')} action={generateControls(false)} />
          {busy && <p className="muted small center">{t('briefing.generating_note', { fallback: 'AI 요약까지 만드는 데 보통 5~15초 걸려요.' })}</p>}
        </div>
      )}

      {briefing && <BriefingDetail briefing={briefing} isPast={false} actions={generateControls(true)} />}
    </>
  )
}

function HistoryTab() {
  const { t, locale, timezone } = useLocale()
  const [page, setPage] = useState(1)
  const [allVersionsOn, setAllVersionsOn] = useState<string | null>(null)
  const [openId, setOpenId] = useState<number | null>(null)

  const params = { page, size: HISTORY_PAGE_SIZE, ...(allVersionsOn ? { latest_only: false, date_from: allVersionsOn, date_to: allVersionsOn } : {}) }
  const list = useQuery({
    queryKey: queryKeys.dailyBriefingHistory(params),
    queryFn: () => api.listDailyBriefings(params),
    placeholderData: keepPreviousData,
  })
  const detail = useQuery({
    queryKey: queryKeys.dailyBriefing(openId ?? -1),
    queryFn: () => api.getDailyBriefing(openId!),
    enabled: openId != null,
  })

  if (openId != null) {
    return (
      <>
        <button type="button" className="back-link-button" onClick={() => setOpenId(null)}>
          ← {t('daily_briefing.history_title')}
        </button>
        {detail.isPending && <p className="muted">{t('common.loading')}</p>}
        {detail.error && <ErrorNotice error={detail.error} onRetry={() => void detail.refetch()} />}
        {detail.data && <BriefingDetail briefing={detail.data} isPast />}
      </>
    )
  }

  const items = list.data?.items ?? []
  const headers = new Set(groupHeaderIndexes(items))
  return (
    <div className="card">
      <h2 className="briefing-title">{t('daily_briefing.history_title')}</h2>
      {allVersionsOn && (
        <p className="small">
          {allVersionsOn} · {t('daily_briefing.all_versions')}{' '}
          <button type="button" className="link-button" onClick={() => { setAllVersionsOn(null); setPage(1) }}>
            {t('common.close')}
          </button>
        </p>
      )}
      {list.isPending && <p className="muted">{t('common.loading')}</p>}
      {list.error && <ErrorNotice error={list.error} onRetry={() => void list.refetch()} />}
      {list.data && items.length === 0 && <EmptyState message={t('daily_briefing.not_generated')} />}

      <ul className="history-list">
        {items.map((item, index) => (
          <li key={item.id}>
            {headers.has(index) && <h3 className="date-group-header">{t(`date_group.${item.date_group}`)}</h3>}
            <div className="history-row">
              <button type="button" className="history-open" onClick={() => setOpenId(item.id)}>
                <span className="history-title">
                  {item.title}{' '}
                  <StatusBadge tone={item.market_data_status === 'latest' ? 'ok' : item.market_data_status === 'delayed' ? 'warn' : 'danger'}>
                    {t(`daily_briefing.market_data_status.${item.market_data_status}`)}
                  </StatusBadge>
                  {item.locale !== locale && <StatusBadge tone="accent">{languageName(toLocale(item.locale), locale)}</StatusBadge>}
                </span>
                <span className="muted small">
                  {item.local_date} {item.local_time} · {t('daily_briefing.version', { vars: { n: item.version } })} · {t('daily_briefing.data_as_of')}{' '}
                  {formatTradingDate(item.data_as_of)}
                </span>
                {/* 지연·오래된 데이터는 헤드라인을 경고로(핸드오프 2-3절) */}
                {item.headline && item.market_data_status !== 'latest' && <span className="small briefing-headline-warn">{item.headline}</span>}
              </button>
              {item.versions_on_date >= 2 && allVersionsOn == null && (
                <button type="button" className="link-button" onClick={() => { setAllVersionsOn(item.local_date); setPage(1) }}>
                  {t('daily_briefing.versions_on_date', { vars: { n: item.versions_on_date } })}
                </button>
              )}
            </div>
          </li>
        ))}
      </ul>
      {list.data && <Pagination offset={(page - 1) * HISTORY_PAGE_SIZE} pageSize={HISTORY_PAGE_SIZE} total={list.data.total} onChange={(offset) => setPage(offset / HISTORY_PAGE_SIZE + 1)} />}
      <p className="muted small">{timezone}</p>
    </div>
  )
}

export default function BriefingPage() {
  const { t } = useLocale()
  const [tab, setTab] = useState<Tab>('today')
  return (
    <div className="container container-wide page briefing-page">
      <h1 className="page-title">{t('nav.briefing')}</h1>
      <div className="segmented briefing-tabs" role="group" aria-label={t('nav.briefing')}>
        <button type="button" className={tab === 'today' ? 'seg active' : 'seg'} aria-pressed={tab === 'today'} onClick={() => setTab('today')}>
          {t('briefing.tab_today', { fallback: '오늘' })}
        </button>
        <button type="button" className={tab === 'history' ? 'seg active' : 'seg'} aria-pressed={tab === 'history'} onClick={() => setTab('history')}>
          {t('briefing.tab_history', { fallback: '지난 브리핑' })}
        </button>
      </div>
      {tab === 'today' ? <TodayTab onShowHistory={() => setTab('history')} /> : <HistoryTab />}
    </div>
  )
}
