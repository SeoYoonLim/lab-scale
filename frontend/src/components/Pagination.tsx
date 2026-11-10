import { useLocale } from '../i18n/useLocale'

interface PaginationProps {
  offset: number
  pageSize: number
  total: number
  onChange: (offset: number) => void
}

export default function Pagination({ offset, pageSize, total, onChange }: PaginationProps) {
  const { t } = useLocale()
  if (total <= 0) return null
  return (
    <nav className="pagination" aria-label={t('pagination.aria', { fallback: '페이지 이동' })}>
      <button type="button" disabled={offset === 0} onClick={() => onChange(Math.max(0, offset - pageSize))}>
        {t('pagination.prev', { fallback: '이전' })}
      </button>
      <span className="muted small" aria-live="polite">
        {offset + 1}–{Math.min(offset + pageSize, total)} / {total}
      </span>
      <button type="button" disabled={offset + pageSize >= total} onClick={() => onChange(offset + pageSize)}>
        {t('pagination.next', { fallback: '다음' })}
      </button>
    </nav>
  )
}
