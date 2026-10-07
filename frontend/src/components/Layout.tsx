import { NavLink, Outlet } from 'react-router-dom'
import { USE_MOCK } from '../api'
import { useAuth } from '../auth/AuthContext'
import DisclaimerFooter from './DisclaimerFooter'

const linkClass = ({ isActive }: { isActive: boolean }) =>
  isActive ? 'sidebar-link active' : 'sidebar-link'

export default function Layout() {
  const { user, logout } = useAuth()

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

        <div className="sidebar-footer">
          {user ? (
            <div className="sidebar-user">
              <span className="sidebar-username">{user.username}</span>
              <button type="button" className="sidebar-logout" onClick={logout}>
                로그아웃
              </button>
            </div>
          ) : (
            <NavLink to="/login" className="sidebar-link">
              로그인
            </NavLink>
          )}
          {USE_MOCK && (
            <span className="mock-flag" title="백엔드 API 연결 전 화면 개발용 샘플 데이터예요">
              샘플 데이터로 보는 중
            </span>
          )}
        </div>
      </aside>
      <main className="main">
        <Outlet />
        <DisclaimerFooter />
      </main>
    </div>
  )
}
