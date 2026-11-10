import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Fragment, useState } from 'react'
import type { FormEvent } from 'react'
import { api } from '../api'
import { groupHeaderIndexes } from '../api/contracts/history'
import type { HistoryItem, HistorySort, HistoryType } from '../api/contracts/history'
import { describeDataBasis } from '../api/contracts/research'
import { queryKeys } from '../api/queryKeys'
import DiagnosisCompareView from '../components/DiagnosisCompareView'
import DiagnosisResultView from '../components/DiagnosisResultView'
import Pagination from '../components/Pagination'
import ResearchAnswerBody from '../components/ResearchAnswerBody'
import SourceFootnotes from '../components/SourceFootnotes'
import Banner from '../components/ui/Banner'
import EmptyState from '../components/ui/EmptyState'
import ErrorNotice from '../components/ui/ErrorNotice'
import StatusBadge from '../components/ui/StatusBadge'
import { LOCALE_NATIVE_NAMES, toLocale } from '../i18n'
import { useLocale } from '../i18n/useLocale'
import { formatPlainPct, formatWon } from '../utils/format'
import { TOOL_LABELS } from '../utils/toolLabels'

const PAGE_SIZE = 10

// 검색어(terms)를 결과 제목·요약에서 강조한다. 정규식 특수문자는 이스케이프한다.
function Highlight({ text, terms }: { text: string; terms: string[] }) {
  if (terms.length === 0 || !text) return <>{text}</>
  const escaped = terms.map((term) => term.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'))
  const parts = text.split(new RegExp(`(${escaped.join('|')})`, 'gi'))
  return (
    <>
      {parts.map((part, i) =>
        i % 2 === 1 ? (
          <mark key={i} className="search-mark">
            {part}
          </mark>
        ) : (
          <Fragment key={i}>{part}</Fragment>
        ),
      )}
    </>
  )
}

function ResearchRecord({ reportId }: { reportId: number }) {
  const { t } = useLocale()
  const record = useQuery({ queryKey: queryKeys.reportRecord(reportId), queryFn: () => api.getReportRecord(reportId) })
  if (record.isPending) return <p className="muted">{t('common.loading')}</p>
  if (record.error) return <ErrorNotice error={record.error} onRetry={() => void record.refetch()} />
  const r = record.data
  return (
    <div className="history-detail">
      {/* 과거 리포트는 당시 저장된 질문·답변·출처 그대로다 - 현재 시세로 다시 만들지 않는다 */}
      <Banner tone="info">{t('history.past_basis_notice')}</Banner>
      <ResearchAnswerBody answer={r.answer} locale={r.locale} translationStatus={r.translation_status} reportId={r.report_id} originalAnswer={r.original_answer} />
      {r.data_basis.length > 0 && (
        <div className="data-basis-block">
          <div className="muted small">{t('research.data_basis')}</div>
          <ul className="plain-list">
            {r.data_basis.map((b, i) => {
              const when = describeDataBasis(b)
              return (
                <li key={`${b.tool}-${b.ticker}-${i}`}>
                  {[b.company_name, TOOL_LABELS[b.tool] ?? b.tool].filter(Boolean).join(' · ')}
                  {when && <span className="muted num"> · {when}</span>}
                </li>
              )
            })}
          </ul>
        </div>
      )}
      <SourceFootnotes usedTools={r.used_tools} sources={r.sources} />
      <p className="muted small">{t('research.source_title_original')}</p>
      <p className="disclaimer-note muted small">{r.disclaimer}</p>
    </div>
  )
}

function DiagnosisRecord({ diagnosisId }: { diagnosisId: number }) {
  const { t } = useLocale()
  const [showCompare, setShowCompare] = useState(false)
  const detail = useQuery({ queryKey: queryKeys.diagnosisDetail(diagnosisId), queryFn: () => api.getDiagnosisDetail(diagnosisId) })
  const compare = useQuery({
    queryKey: queryKeys.diagnosisCompare(diagnosisId),
    queryFn: () => api.compareDiagnosis(diagnosisId),
    enabled: showCompare,
  })
  return (
    <div className="history-detail">
      <Banner tone="info">{t('history.past_basis_notice')}</Banner>
      {detail.isPending && <p className="muted">{t('common.loading')}</p>}
      {detail.error && <ErrorNotice error={detail.error} onRetry={() => void detail.refetch()} />}
      {detail.data && <DiagnosisResultView result={detail.data} />}
      <button type="button" className="reset-button" onClick={() => setShowCompare((v) => !v)}>
        {showCompare ? t('common.close') : t('history.compare')}
      </button>
      {showCompare && (
        <div className="diagnosis-compare-panel">
          {compare.isPending && <p className="muted">{t('common.loading')}</p>}
          {compare.error && <ErrorNotice error={compare.error} onRetry={() => void compare.refetch()} />}
          {compare.data && <DiagnosisCompareView compare={compare.data} />}
        </div>
      )}
    </div>
  )
}

function HistoryCard({ item, terms, onDelete, deleting }: { item: HistoryItem; terms: string[]; onDelete: () => void; deleting: boolean }) {
  const { t, locale } = useLocale()
  const [open, setOpen] = useState(false)
  const itemLocale = toLocale(item.locale)

  return (
    <article className="history-card">
      <header className="history-card-head">
        <StatusBadge tone={item.kind === 'research' ? 'accent' : 'neutral'}>
          {item.kind === 'research' ? t('history.kind_research') : t('history.kind_diagnosis')}
        </StatusBadge>
        {item.research && <StatusBadge>{t(`analysis_type.${item.research.analysis_type}`)}</StatusBadge>}
        {/* 기록의 작성 언어가 지금 화면 언어와 다르면 표시한다(핸드오프 2-6절) */}
        {itemLocale !== locale && <StatusBadge tone="warn">{t('common.written_in', { vars: { language: LOCALE_NATIVE_NAMES[itemLocale] } })}</StatusBadge>}
        <span className="muted small num history-card-time">
          {item.local_date} {item.local_time}
        </span>
      </header>

      <h3 className="history-card-title">
        <button type="button" className="history-open" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
          <Highlight text={item.title} terms={terms} />
        </button>
      </h3>
      {item.company_names.length > 0 && (
        <p className="meta small">
          {item.company_names.slice(0, 4).map((name) => (
            <StatusBadge key={name}>{name}</StatusBadge>
          ))}
        </p>
      )}
      {item.summary && (
        <p className="muted small history-card-summary">
          <Highlight text={item.summary} terms={terms} />
        </p>
      )}

      {item.research && <p className="muted small">{t('history.source_count', { vars: { count: item.research.source_count } })}</p>}
      {item.diagnosis && (
        <div className="small">
          <p className="num">
            {t('diagnosis.total_asset')} {formatWon(item.diagnosis.total_asset)} · {t('diagnosis.cash_weight')} {formatPlainPct(item.diagnosis.cash_weight_pct)}
          </p>
          {item.diagnosis.top_holdings.length > 0 && (
            <p className="muted">
              {t('diagnosis.top_holdings')}:{' '}
              {item.diagnosis.top_holdings.map((h) => `${h.company_name} ${formatPlainPct(h.weight_pct)}`).join(', ')}
            </p>
          )}
          {item.diagnosis.risk_summary && (
            <p className="muted">
              {t('diagnosis.risk_summary')}: {item.diagnosis.risk_summary}
            </p>
          )}
          {item.diagnosis.flag_codes.length > 0 && (
            <p className="meta">
              {item.diagnosis.flag_codes.map((code, i) => (
                <StatusBadge key={`${code}-${i}`} tone="warn">
                  {t(`diagnosis_flag.${code}`, { fallback: code })}
                </StatusBadge>
              ))}
            </p>
          )}
        </div>
      )}

      <div className="history-card-actions">
        <button type="button" className="diagnosis-history-toggle" onClick={() => setOpen((v) => !v)}>
          {open ? t('common.close') : t('history.open_detail')}
        </button>
        <button type="button" className="delete-report" onClick={onDelete} disabled={deleting}>
          삭제
        </button>
      </div>

      {open && (item.kind === 'research' ? <ResearchRecord reportId={item.id} /> : <DiagnosisRecord diagnosisId={item.id} />)}
    </article>
  )
}

export default function HistoryPage() {
  const { t, timezone } = useLocale()
  const queryClient = useQueryClient()
  const [type, setType] = useState<HistoryType>('all')
  const [keywordInput, setKeywordInput] = useState('')
  const [keyword, setKeyword] = useState('')
  const [dateFrom, setDateFrom] = useState('')
  const [dateTo, setDateTo] = useState('')
  const [sort, setSort] = useState<HistorySort>('created_at,desc')
  const [page, setPage] = useState(1)

  // 시작일이 종료일보다 늦으면 서버에 보내기 전에 막는다(서버도 422지만 그 전에 이유를 보여준다, history.error_date_range)
  const dateError = dateFrom !== '' && dateTo !== '' && dateFrom > dateTo
  const params = {
    type,
    keyword: keyword || undefined,
    date_from: dateFrom || undefined,
    date_to: dateTo || undefined,
    timezone,
    page,
    size: PAGE_SIZE,
    sort,
  }
  const search = useQuery({
    queryKey: queryKeys.historySearch(params),
    queryFn: () => api.searchHistory(params),
    enabled: !dateError,
    placeholderData: keepPreviousData,
  })

  const remove = useMutation({
    mutationFn: (item: HistoryItem) => (item.kind === 'research' ? api.deleteReport(item.id) : api.deleteDiagnosis(item.id)),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['historySearch'] })
      void queryClient.invalidateQueries({ queryKey: ['diagnosisHistory'] })
    },
  })

  function handleDelete(item: HistoryItem) {
    if (!confirm(item.kind === 'research' ? '이 리포트를 삭제할까요? 되돌릴 수 없어요.' : '이 진단을 삭제할까요? 되돌릴 수 없어요.')) return
    remove.mutate(item)
  }

  function handleSearch(e: FormEvent) {
    e.preventDefault()
    setKeyword(keywordInput.trim())
    setPage(1)
  }

  const data = search.data
  const items = data?.items ?? []
  const headers = new Set(groupHeaderIndexes(items))
  const filtered = keyword !== '' || dateFrom !== '' || dateTo !== ''
  const tabs: { value: HistoryType; label: string; count: number | undefined }[] = [
    { value: 'all', label: t('history.tab_all'), count: data?.counts.all },
    { value: 'research', label: t('history.tab_research'), count: data?.counts.research },
    { value: 'diagnosis', label: t('history.tab_diagnosis'), count: data?.counts.diagnosis },
  ]

  return (
    <div className="container container-wide page history-page">
      <h1 className="page-title">{t('history.title')}</h1>

      <form className="history-filters card" onSubmit={handleSearch}>
        <div className="history-search-row">
          <input
            type="search"
            value={keywordInput}
            onChange={(e) => setKeywordInput(e.target.value)}
            placeholder={t('history.search_placeholder')}
            aria-label={t('history.search_placeholder')}
          />
          <button type="submit" className="send">
            {t('common.search')}
          </button>
        </div>
        <div className="history-filter-row">
          <label className="inline-select">
            <span className="muted small">{t('history.date_from')}</span>
            <input type="date" value={dateFrom} onChange={(e) => { setDateFrom(e.target.value); setPage(1) }} />
          </label>
          <label className="inline-select">
            <span className="muted small">{t('history.date_to')}</span>
            <input type="date" value={dateTo} onChange={(e) => { setDateTo(e.target.value); setPage(1) }} />
          </label>
          <label className="inline-select">
            <select value={sort} onChange={(e) => { setSort(e.target.value as HistorySort); setPage(1) }} aria-label={t('history.sort_newest')}>
              <option value="created_at,desc">{t('history.sort_newest')}</option>
              <option value="created_at,asc">{t('history.sort_oldest')}</option>
            </select>
          </label>
          {filtered && (
            <button
              type="button"
              className="link-button"
              onClick={() => {
                setKeywordInput('')
                setKeyword('')
                setDateFrom('')
                setDateTo('')
                setPage(1)
              }}
            >
              {t('common.reset')}
            </button>
          )}
        </div>
        {dateError && <p className="notice notice-error">{t('history.error_date_range')}</p>}
      </form>

      <div className="history-tabs" role="tablist">
        {tabs.map((tab) => (
          <button key={tab.value} type="button" role="tab" aria-selected={type === tab.value} className={type === tab.value ? 'history-tab active' : 'history-tab'} onClick={() => { setType(tab.value); setPage(1) }}>
            {tab.label}
            {tab.count != null && <span className="history-tab-count num">{tab.count}</span>}
          </button>
        ))}
      </div>

      {search.isPending && !dateError && <p className="muted">{t('common.loading')}</p>}
      {search.error && <ErrorNotice error={search.error} onRetry={() => void search.refetch()} fallback={t('history.error_load')} />}
      {remove.error && <ErrorNotice error={remove.error} />}

      {data && items.length === 0 && (
        <div className="card">
          <EmptyState message={filtered ? t('history.empty_search') : t('history.empty')} />
        </div>
      )}

      {items.length > 0 && (
        <div className={search.isPlaceholderData ? 'is-stale' : undefined}>
          {items.map((item, index) => (
            <Fragment key={`${item.kind}-${item.id}`}>
              {headers.has(index) && <h2 className="date-group-header">{t(`date_group.${item.date_group}`)}</h2>}
              <HistoryCard item={item} terms={data?.terms ?? []} onDelete={() => handleDelete(item)} deleting={remove.isPending} />
            </Fragment>
          ))}
        </div>
      )}

      {data && (
        <>
          <p className="muted small center">{t('common.total_count', { vars: { total: data.total } })}</p>
          <Pagination offset={(page - 1) * PAGE_SIZE} pageSize={PAGE_SIZE} total={data.total} onChange={(offset) => setPage(offset / PAGE_SIZE + 1)} />
        </>
      )}
      <p className="muted small center">{timezone}</p>
    </div>
  )
}
