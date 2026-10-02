import { useState } from 'react'
import { api } from '../api'
import SourceFootnotes from '../components/SourceFootnotes'
import { useAsync } from '../hooks/useAsync'
import type { ReportDetail, ReportListItem } from '../types'
import { formatDate } from '../utils/format'
import { TOOL_LABELS } from '../utils/toolLabels'

const PAGE_SIZE = 20

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
    <li className="ledger-row">
      <button type="button" className="ledger-summary" onClick={() => void toggle()}>
        <div className="ledger-head">
          <span className="ledger-number num">#{item.report_id}</span>
          <span className="ledger-question">{item.question}</span>
        </div>
        <div className="meta muted small">
          <span className="num">{formatDate(item.created_at)}</span>
          {item.company_name && <span>{item.company_name}</span>}
          {item.used_tools.length > 0 && (
            <span>{item.used_tools.map((t) => TOOL_LABELS[t] ?? t).join(', ')}</span>
          )}
          {item.previous_report_id != null && <span>#{item.previous_report_id}에 이어서</span>}
        </div>
      </button>

      {open && (
        <div className="ledger-detail">
          {loading && <p className="muted">불러오는 중…</p>}
          {loadError && <p className="notice notice-error">{loadError}</p>}
          {detail && (
            <>
              <p className="ledger-answer">{detail.answer}</p>
              <SourceFootnotes usedTools={detail.used_tools} sources={detail.sources} />
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

      {items.length > 0 && (
        <div className="card">
          <ul className="ledger">
            {items.map((item) => (
              <ReportRow key={item.report_id} item={item} onDeleted={handleDeleted} />
            ))}
          </ul>
        </div>
      )}

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
