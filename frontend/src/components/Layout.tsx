import { NavLink, Outlet } from 'react-router-dom'
import { USE_MOCK } from '../api'

const navClass = ({ isActive }: { isActive: boolean }) =>
  isActive ? 'nav-link active' : 'nav-link'

export default function Layout() {
  return (
    <div className="app">
      <header className="topbar">
        <div className="topbar-inner">
          <NavLink to="/" className="brand">
            AI 투자 리서치
          </NavLink>
          <nav className="nav">
            <NavLink to="/" end className={navClass}>
              리서치
            </NavLink>
            <NavLink to="/companies" className={navClass}>
              종목
            </NavLink>
            <NavLink to="/reports" className={navClass}>
              기록
            </NavLink>
          </nav>
          {USE_MOCK && (
            <span className="mock-flag" title="백엔드 API 연결 전 화면 개발용 샘플 데이터예요">
              샘플 데이터
            </span>
          )}
        </div>
      </header>
      <main className="main">
        <Outlet />
      </main>
    </div>
  )
}
