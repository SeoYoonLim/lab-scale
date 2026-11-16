import type { ReactNode } from 'react'
import { useLocale } from '../../i18n/useLocale'

// "불러오는 중" 표시. 화면 읽기 프로그램이 읽을 수 있게 role="status"를 붙인 공통 조각이다(오류는 ErrorNotice가 role="alert").
export default function LoadingNotice({ small = false, className = '', children }: { small?: boolean; className?: string; children?: ReactNode }) {
  const { t } = useLocale()
  const classes = ['muted', small ? 'small' : '', className].filter(Boolean).join(' ')
  return (
    <p className={classes} role="status">
      {children ?? t('common.loading')}
    </p>
  )
}
