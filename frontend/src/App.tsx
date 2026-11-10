import { lazy } from 'react'
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import RequireAuth from './components/RequireAuth'
import Layout from './components/Layout'
import LoginPage from './pages/LoginPage'
import SignupPage from './pages/SignupPage'
import './App.css'

// 화면은 처음 들어갈 때 불러온다(경로별 코드 분할). 로그인·가입·레이아웃만 첫 번들에 둔다.
const BriefingPage = lazy(() => import('./pages/BriefingPage'))
const CompanyListPage = lazy(() => import('./pages/CompanyListPage'))
const CompanyPage = lazy(() => import('./pages/CompanyPage'))
const HistoryPage = lazy(() => import('./pages/HistoryPage'))
const PortfolioPage = lazy(() => import('./pages/PortfolioPage'))
const ResearchPage = lazy(() => import('./pages/ResearchPage'))
const SettingsPage = lazy(() => import('./pages/SettingsPage'))
const DiagnosisTab = lazy(() => import('./pages/portfolio/DiagnosisTab'))
const ExposureSection = lazy(() => import('./pages/portfolio/ExposureSection'))
const HoldingsTab = lazy(() => import('./pages/portfolio/HoldingsTab'))
const PerformanceSection = lazy(() => import('./pages/portfolio/PerformanceSection'))
const TradesSection = lazy(() => import('./pages/portfolio/TradesSection'))
const WatchlistSection = lazy(() => import('./pages/portfolio/WatchlistSection'))

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
            path="briefing"
            element={
              <RequireAuth>
                <BriefingPage />
              </RequireAuth>
            }
          />
          <Route
            path="history"
            element={
              <RequireAuth>
                <HistoryPage />
              </RequireAuth>
            }
          />
          <Route
            path="settings"
            element={
              <RequireAuth>
                <SettingsPage />
              </RequireAuth>
            }
          />
          {/* 예전 주소(리포트 기록)는 새 "내 기록"으로 */}
          <Route path="reports" element={<Navigate to="/history" replace />} />
          <Route
            path="portfolio"
            element={
              <RequireAuth>
                <PortfolioPage />
              </RequireAuth>
            }
          >
            <Route index element={<HoldingsTab />} />
            <Route path="performance" element={<PerformanceSection />} />
            <Route path="exposure" element={<ExposureSection />} />
            <Route path="diagnosis" element={<DiagnosisTab />} />
            <Route path="trades" element={<TradesSection />} />
            <Route path="watchlist" element={<WatchlistSection />} />
          </Route>
          <Route path="*" element={<div className="container page">페이지를 찾을 수 없어요.</div>} />
        </Route>
      </Routes>
    </BrowserRouter>
  )
}
