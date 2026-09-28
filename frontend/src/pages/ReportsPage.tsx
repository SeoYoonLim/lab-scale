import { useState } from 'react'
import { api } from '../api'
import { useAsync } from '../hooks/useAsync'
import type { ReportDetail, ReportListItem } from '../types'
import { formatDate } from '../utils/format'

const PAGE_SIZE = 20

const TOOL_LABELS: Record<string, string> = {
  stock_tool: '주가',
  news_tool: '뉴스',
  disclosure_tool: '공시',
  rag_search_tool: '의미 검색',
  market_tool: '시장 지수',
}

function ReportRow({ item, onDeleted }: { item: ReportListItem; onDeleted: (reportId: number) => void }) {
  const [open, setOpen] = useState(false)
  const [detail, setDetail] = useState<ReportDetail | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [deleting, setDeleting] = useState(false)

  async function toggle() {
    if (open) {
      setOpen(false)
      return
    }
    setOpen(true)
    if (detail) return
    setLoading(true)
    setLoadError(null)
    try {
      setDetail(await api.getReport(item.report_id))
    } catch (error) {
      setLoadError(error instanceof Error ? error.message : '불러오지 못했어요.')
    } finally {
      setLoading(false)
    }
  }

  async function handleDelete() {
    if (!confirm('이 리포트를 삭제할까요? 되돌릴 수 없어요.')) return
    setDeleting(true)
    try {
      await api.deleteReport(item.report_id)
      onDeleted(item.report_id)
    } catch (error) {
      setLoadError(error instanceof Error ? error.message : '삭제하지 못했어요.')
      setDeleting(false)
    }
  }

  return (
    <li className="report-row">
      <button type="button" className="report-summary" onClick={() => void toggle()}>
        <div className="report-summary-main">
          <span className="report-question">{item.question}</span>
          {item.company_name && <span className="report-company">{item.company_name}</span>}
        </div>
        <div className="report-summary-meta muted small">
          {formatDate(item.created_at)}
          {item.used_tools.length > 0 &&
            ` · ${item.used_tools.map((t) => TOOL_LABELS[t] ?? t).join(', ')}`}
          {item.previous_report_id != null && ' · 후속 질문'}
        </div>
      </button>

      {open && (
        <div className="report-detail">
          {loading && <p className="muted">불러오는 중…</p>}
          {loadError && <p className="notice notice-error">{loadError}</p>}
          {detail && (
            <>
              <p className="report-answer">{detail.answer}</p>
              {detail.sources.length > 0 && (
                <ul className="source-list">
                  {detail.sources.map((source, index) => (
                    <li key={index}>
                      {source.url ? (
                        <a href={source.url} target="_blank" rel="noreferrer">
                          {source.title}
                        </a>
                      ) : (
                        <span>{source.title}</span>
                      )}
                    </li>
                  ))}
                </ul>
              )}
            </>
          )}
          <button type="button" className="delete-report" onClick={() => void handleDelete()} disabled={deleting}>
            {deleting ? '삭제 중…' : '삭제'}
          </button>
        </div>
      )}
    </li>
  )
}

export default function ReportsPage() {
  const [offset, setOffset] = useState(0)
  const [removedIds, setRemovedIds] = useState<Set<number>>(new Set())
  const { data, error, loading } = useAsync(() => api.listReports(PAGE_SIZE, offset), [offset])

  const items = data?.items.filter((item) => !removedIds.has(item.report_id)) ?? []

  function handleDeleted(reportId: number) {
    setRemovedIds((prev) => new Set(prev).add(reportId))
  }

  return (
    <div className="container page">
      <h1 className="page-title">리포트 기록</h1>

      {loading && !data && <p className="muted">불러오는 중…</p>}
      {error && <p className="notice notice-error">{error.message}</p>}
      {data && items.length === 0 && <p className="muted">아직 저장된 리포트가 없어요. 리서치 탭에서 질문해보세요.</p>}

      <ul className="report-list">
        {items.map((item) => (
          <ReportRow key={item.report_id} item={item} onDeleted={handleDeleted} />
        ))}
      </ul>

      {data && data.total > 0 && (
        <div className="pagination">
          <button type="button" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>
            이전
          </button>
          <span className="muted small">
            {offset + 1}–{Math.min(offset + PAGE_SIZE, data.total)} / {data.total}
          </span>
          <button
            type="button"
            disabled={offset + PAGE_SIZE >= data.total}
            onClick={() => setOffset(offset + PAGE_SIZE)}
          >
            다음
          </button>
        </div>
      )}
    </div>
  )
}
