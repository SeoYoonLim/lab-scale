import { Suspense } from 'react'
import type { ReactNode } from 'react'
import { NavLink, Outlet } from 'react-router-dom'
import { USE_MOCK } from '../api'
import { useAuth } from '../auth/useAuth'
import { useLocale } from '../i18n/useLocale'
import DisclaimerFooter from './DisclaimerFooter'
import { IconBulb, IconChart, IconFile, IconHome, IconSettings, IconSun, IconWallet } from './icons'

const linkClass = ({ isActive }: { isActive: boolean }) =>
  isActive ? 'sidebar-link active' : 'sidebar-link'

export default function Layout() {
  const { user, logout } = useAuth()
  const { t } = useLocale()

  // 라벨은 백엔드 리소스의 nav.* 와 언어 설정을 따른다(없는 키는 한국어 fallback)
  const items: { to: string; label: string; icon: ReactNode; end?: boolean; auth?: boolean }[] = [
    { to: '/', label: t('nav.research'), icon: <IconHome />, end: true, auth: true },
    { to: '/briefing', label: t('nav.briefing'), icon: <IconSun />, auth: true },
    { to: '/companies', label: t('nav.companies', { fallback: '종목 분석' }), icon: <IconChart /> },
    { to: '/portfolio', label: t('nav.portfolio', { fallback: '포트폴리오' }), icon: <IconWallet />, auth: true },
    { to: '/history', label: t('nav.my_records'), icon: <IconFile />, auth: true },
    { to: '/settings', label: t('nav.settings'), icon: <IconSettings />, auth: true },
  ]

  return (
    <div className="app-shell">
      <a href="#main" className="skip-link">
        본문으로 건너뛰기
      </a>
      <aside className="sidebar">
        <NavLink to={user ? '/' : '/companies'} className="sidebar-brand">
          <span className="brand-mark">AI</span>
          <span className="brand-text">
            <span className="brand-name">투자 리서치 에이전트</span>
            <span className="brand-tagline">AI가 찾아주는 스마트한 투자 인사이트</span>
          </span>
        </NavLink>

        <nav className="sidebar-nav" aria-label="주 메뉴">
          {items
            .filter((item) => !item.auth || user)
            .map((item) => (
              <NavLink key={item.to} to={item.to} end={item.end} className={linkClass}>
                <span className="sidebar-link-icon">{item.icon}</span>
                <span>{item.label}</span>
              </NavLink>
            ))}
        </nav>

        <div className="sidebar-footer">
          <div className="sidebar-tip">
            <span className="sidebar-tip-icon">
              <IconBulb />
            </span>
            <p>AI Agent가 주가·뉴스·공시를 근거로 분석해요. 결과는 투자 권유가 아니에요.</p>
          </div>
          {user ? (
            <div className="sidebar-user">
              <span className="sidebar-username">{user.username}</span>
              <button type="button" className="sidebar-logout" onClick={logout}>
                {t('nav.logout', { fallback: '로그아웃' })}
              </button>
            </div>
          ) : (
            <NavLink to="/login" className="sidebar-link">
              <span>{t('nav.login', { fallback: '로그인' })}</span>
            </NavLink>
          )}
          {USE_MOCK && (
            <span className="mock-flag" title="백엔드 API 연결 전 화면 개발용 샘플 데이터예요">
              샘플 데이터로 보는 중
            </span>
          )}
        </div>
      </aside>
      <main className="main" id="main" tabIndex={-1}>
        <Suspense fallback={<p className="muted container page">{t('common.loading')}</p>}>
          <Outlet />
        </Suspense>
        <DisclaimerFooter />
      </main>
    </div>
  )
}
