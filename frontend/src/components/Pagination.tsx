interface PaginationProps {
  offset: number
  pageSize: number
  total: number
  onChange: (offset: number) => void
}

export default function Pagination({ offset, pageSize, total, onChange }: PaginationProps) {
  if (total <= 0) return null
  return (
    <nav className="pagination" aria-label="페이지 이동">
      <button type="button" disabled={offset === 0} onClick={() => onChange(Math.max(0, offset - pageSize))}>
        이전
      </button>
      <span className="muted small" aria-live="polite">
        {offset + 1}–{Math.min(offset + pageSize, total)} / {total}
      </span>
      <button type="button" disabled={offset + pageSize >= total} onClick={() => onChange(offset + pageSize)}>
        다음
      </button>
    </nav>
  )
}
