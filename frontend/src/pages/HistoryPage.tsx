import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Fragment, useEffect, useId, useRef, useState } from 'react'
import type { FormEvent, KeyboardEvent } from 'react'
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
import { languageName, toLocale } from '../i18n'
import { useLocale } from '../i18n/useLocale'
import { formatPlainPct, formatWon } from '../utils/format'
import { toolLabel } from '../utils/toolLabels'

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
                  {[b.company_name, toolLabel(t, b.tool)].filter(Boolean).join(' · ')}
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
  // 삭제는 되돌릴 수 없어서 한 번 더 묻는다. 브라우저 기본 confirm 대신 카드 안에 확인 영역을 펼친다(디자인·접근성·테스트 모두 낫다).
  // 펼치면 "취소"에 포커스를 둬서 Enter를 잘못 눌러도 지워지지 않고, Esc로 닫을 수 있다.
  const [confirming, setConfirming] = useState(false)
  const cancelRef = useRef<HTMLButtonElement>(null)
  const confirmId = useId()
  const itemLocale = toLocale(item.locale)
  const question =
    item.kind === 'research'
      ? t('history.delete_research_confirm', { fallback: '이 리포트를 삭제할까요? 되돌릴 수 없어요.' })
      : t('diagnosis.delete_confirm', { fallback: '이 진단을 삭제할까요? 되돌릴 수 없어요.' })

  useEffect(() => {
    if (confirming) cancelRef.current?.focus()
  }, [confirming])

  return (
    <article className="history-card">
      <header className="history-card-head">
        <StatusBadge tone={item.kind === 'research' ? 'accent' : 'neutral'}>
          {item.kind === 'research' ? t('history.kind_research') : t('history.kind_diagnosis')}
        </StatusBadge>
        {item.research && <StatusBadge>{t(`analysis_type.${item.research.analysis_type}`)}</StatusBadge>}
        {/* 기록의 작성 언어가 지금 화면 언어와 다르면 표시한다(핸드오프 2-6절) */}
        {itemLocale !== locale && <StatusBadge tone="warn">{t('common.written_in', { vars: { language: languageName(itemLocale, locale) } })}</StatusBadge>}
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

      {confirming ? (
        <div
          className="history-confirm"
          role="alertdialog"
          aria-labelledby={confirmId}
          onKeyDown={(e) => {
            if (e.key === 'Escape' && !deleting) setConfirming(false)
          }}
        >
          <p id={confirmId} className="history-confirm-text">
            {question}
          </p>
          <div className="history-confirm-actions">
            <button ref={cancelRef} type="button" className="btn" onClick={() => setConfirming(false)} disabled={deleting}>
              {t('common.cancel')}
            </button>
            <button type="button" className="btn btn-danger-solid" onClick={onDelete} disabled={deleting}>
              {deleting ? t('common.loading') : t('common.delete', { fallback: '삭제' })}
            </button>
          </div>
        </div>
      ) : (
        <div className="history-card-actions">
          <button type="button" className="btn" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
            {open ? t('common.close') : t('history.open_detail')}
          </button>
          <button type="button" className="btn btn-danger" onClick={() => setConfirming(true)}>
            {t('common.delete', { fallback: '삭제' })}
          </button>
        </div>
      )}

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

  // 삭제 확인은 카드 안의 확인 영역이 이미 했다
  function handleDelete(item: HistoryItem) {
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

  const tabRefs = useRef<Partial<Record<HistoryType, HTMLButtonElement | null>>>({})
  // WAI-ARIA 탭 패턴: 방향키로 옮기면 그 탭이 선택되고 포커스가 따라간다(선택된 탭만 Tab 키 순서에 든다)
  function handleTabKeyDown(e: KeyboardEvent<HTMLDivElement>) {
    const order = tabs.map((tab) => tab.value)
    const current = order.indexOf(type)
    let next = current
    if (e.key === 'ArrowRight') next = (current + 1) % order.length
    else if (e.key === 'ArrowLeft') next = (current - 1 + order.length) % order.length
    else if (e.key === 'Home') next = 0
    else if (e.key === 'End') next = order.length - 1
    else return
    e.preventDefault()
    setType(order[next])
    setPage(1)
    tabRefs.current[order[next]]?.focus()
  }

  return (
    <div className="container container-wide page history-page">
      <h1 className="page-title">{t('history.title')}</h1>

      <form className="history-filters card" onSubmit={handleSearch} role="search">
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
          <label className="history-field">
            <span>{t('history.date_from')}</span>
            <input
              type="date"
              value={dateFrom}
              aria-invalid={dateError}
              onChange={(e) => {
                setDateFrom(e.target.value)
                setPage(1)
              }}
            />
          </label>
          <label className="history-field">
            <span>{t('history.date_to')}</span>
            <input
              type="date"
              value={dateTo}
              aria-invalid={dateError}
              onChange={(e) => {
                setDateTo(e.target.value)
                setPage(1)
              }}
            />
          </label>
          <label className="history-field history-field-sort">
            <span>{t('history.sort_label', { fallback: '정렬' })}</span>
            <select
              value={sort}
              onChange={(e) => {
                setSort(e.target.value as HistorySort)
                setPage(1)
              }}
            >
              <option value="created_at,desc">{t('history.sort_newest')}</option>
              <option value="created_at,asc">{t('history.sort_oldest')}</option>
            </select>
          </label>
          {filtered && (
            <button
              type="button"
              className="btn history-reset"
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
        {dateError && (
          <p className="notice notice-error" role="alert">
            {t('history.error_date_range')}
          </p>
        )}
      </form>

      <div className="history-tabs" role="tablist" aria-label={t('history.tabs_aria', { fallback: '기록 유형' })} onKeyDown={handleTabKeyDown}>
        {tabs.map((tab) => (
          <button
            key={tab.value}
            ref={(node) => {
              tabRefs.current[tab.value] = node
            }}
            id={`history-tab-${tab.value}`}
            type="button"
            role="tab"
            aria-selected={type === tab.value}
            aria-controls="history-panel"
            tabIndex={type === tab.value ? 0 : -1}
            className={type === tab.value ? 'history-tab active' : 'history-tab'}
            onClick={() => {
              setType(tab.value)
              setPage(1)
            }}
          >
            <span>{tab.label}</span>
            {tab.count != null && <span className="history-tab-count num">{tab.count}</span>}
          </button>
        ))}
      </div>

      <div id="history-panel" role="tabpanel" aria-labelledby={`history-tab-${type}`}>
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
              <HistoryCard
                item={item}
                terms={data?.terms ?? []}
                onDelete={() => handleDelete(item)}
                deleting={remove.isPending && remove.variables?.kind === item.kind && remove.variables?.id === item.id}
              />
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
      </div>
      <p className="muted small center">{timezone}</p>
    </div>
  )
}
