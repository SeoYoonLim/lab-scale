import { useLocale } from '../i18n/useLocale'

interface PaginationProps {
  offset: number
  pageSize: number
  total: number
  onChange: (offset: number) => void
}

// 끝 쪽에서 방금 누른 버튼이 꺼지면(disabled) 키보드 초점이 body로 빠진다. 그래서 disabled 대신 aria-disabled로 두고
// 눌러도 아무 일도 없게 해서 초점이 그 자리에 남게 한다.
export default function Pagination({ offset, pageSize, total, onChange }: PaginationProps) {
  const { t } = useLocale()
  if (total <= 0) return null
  const atStart = offset === 0
  const atEnd = offset + pageSize >= total
  return (
    <nav className="pagination" aria-label={t('pagination.aria', { fallback: '페이지 이동' })}>
      <button type="button" aria-disabled={atStart || undefined} onClick={() => !atStart && onChange(Math.max(0, offset - pageSize))}>
        {t('pagination.prev', { fallback: '이전' })}
      </button>
      <span className="muted small" aria-live="polite">
        {offset + 1}–{Math.min(offset + pageSize, total)} / {total}
      </span>
      <button type="button" aria-disabled={atEnd || undefined} onClick={() => !atEnd && onChange(offset + pageSize)}>
        {t('pagination.next', { fallback: '다음' })}
      </button>
    </nav>
  )
}
