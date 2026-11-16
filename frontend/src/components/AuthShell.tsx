import type { ReactNode } from 'react'
import { useLocale } from '../i18n/useLocale'

// 로그인·가입·계정 복구처럼 사이드바(Layout) 없이 단독으로 보이는 화면의 틀.
// Layout과 똑같이 "본문으로 건너뛰기" 링크와 <main> 랜드마크를 제공한다.
export default function AuthShell({ children }: { children: ReactNode }) {
  const { t } = useLocale()
  return (
    <>
      <a href="#main" className="skip-link">
        {t('layout.skip', { fallback: '본문으로 건너뛰기' })}
      </a>
      <main className="auth-page" id="main" tabIndex={-1}>
        {children}
      </main>
    </>
  )
}
