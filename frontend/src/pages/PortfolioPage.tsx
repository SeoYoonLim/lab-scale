import { NavLink, Outlet } from 'react-router-dom'
import { useLocale } from '../i18n/useLocale'

// 포트폴리오 화면은 영역이 많아져서(보유·주문, 성과, 실질 노출, AI 진단, 체결, 관심종목) 탭으로 나눈다.
// 각 탭이 자기 데이터를 불러오므로, 안 보는 탭의 API는 호출하지 않는다.
export default function PortfolioPage() {
  const { t } = useLocale()
  const tabs = [
    { to: '/portfolio', end: true, label: t('portfolio.tabs.holdings', { fallback: '보유·주문' }) },
    { to: '/portfolio/performance', label: t('portfolio.tabs.performance', { fallback: '성과' }) },
    { to: '/portfolio/exposure', label: t('portfolio.tabs.exposure', { fallback: '실질 노출' }) },
    { to: '/portfolio/diagnosis', label: t('portfolio.tabs.diagnosis', { fallback: 'AI 진단' }) },
    { to: '/portfolio/trades', label: t('portfolio.tabs.trades', { fallback: '체결 내역' }) },
    { to: '/portfolio/watchlist', label: t('portfolio.tabs.watchlist', { fallback: '관심종목' }) },
  ]

  return (
    <div className="container container-wide page portfolio-page">
      <h1 className="page-title">{t('portfolio.title', { fallback: '포트폴리오' })}</h1>
      <nav className="portfolio-tabs" aria-label={t('portfolio.title', { fallback: '포트폴리오' })}>
        {tabs.map((tab) => (
          <NavLink key={tab.to} to={tab.to} end={tab.end} className={({ isActive }) => (isActive ? 'portfolio-tab active' : 'portfolio-tab')}>
            {tab.label}
          </NavLink>
        ))}
      </nav>
      <Outlet />
    </div>
  )
}
