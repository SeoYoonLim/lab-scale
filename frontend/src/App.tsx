import { BrowserRouter, Route, Routes } from 'react-router-dom'
import RequireAuth from './components/RequireAuth'
import Layout from './components/Layout'
import CompanyListPage from './pages/CompanyListPage'
import CompanyPage from './pages/CompanyPage'
import LoginPage from './pages/LoginPage'
import PortfolioPage from './pages/PortfolioPage'
import ReportsPage from './pages/ReportsPage'
import ResearchPage from './pages/ResearchPage'
import SignupPage from './pages/SignupPage'
import './App.css'

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        {/* 로그인 화면은 사이드바 없이 단독으로 보여준다. */}
        <Route path="login" element={<LoginPage />} />
        <Route path="signup" element={<SignupPage />} />

        <Route element={<Layout />}>
          {/* 종목 검색/상세는 로그인 없이도 된다(backend/API.md "인증" 참고). */}
          <Route path="companies" element={<CompanyListPage />} />
          <Route path="companies/:ticker" element={<CompanyPage />} />

          <Route
            index
            element={
              <RequireAuth>
                <ResearchPage />
              </RequireAuth>
            }
          />
          <Route
            path="reports"
            element={
              <RequireAuth>
                <ReportsPage />
              </RequireAuth>
            }
          />
          <Route
            path="portfolio"
            element={
              <RequireAuth>
                <PortfolioPage />
              </RequireAuth>
            }
          />
          <Route path="*" element={<div className="container page">페이지를 찾을 수 없어요.</div>} />
        </Route>
      </Routes>
    </BrowserRouter>
  )
}
