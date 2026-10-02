import { NavLink, Outlet } from 'react-router-dom'
import { USE_MOCK } from '../api'

const linkClass = ({ isActive }: { isActive: boolean }) =>
  isActive ? 'sidebar-link active' : 'sidebar-link'

export default function Layout() {
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <NavLink to="/" className="sidebar-brand">
          <span className="brand-mark">AI</span>
          <span>투자 리서치</span>
        </NavLink>

        <nav className="sidebar-nav">
          <NavLink to="/" end className={linkClass}>
            리서치
          </NavLink>
          <NavLink to="/companies" className={linkClass}>
            종목
          </NavLink>
          <NavLink to="/reports" className={linkClass}>
            기록
          </NavLink>
          <NavLink to="/portfolio" className={linkClass}>
            포트폴리오
          </NavLink>
        </nav>

        {USE_MOCK && (
          <div className="sidebar-footer">
            <span className="mock-flag" title="백엔드 API 연결 전 화면 개발용 샘플 데이터예요">
              샘플 데이터로 보는 중
            </span>
          </div>
        )}
      </aside>
      <main className="main">
        <Outlet />
      </main>
    </div>
  )
}
