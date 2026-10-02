import { BrowserRouter, Route, Routes } from 'react-router-dom'
import Layout from './components/Layout'
import CompanyListPage from './pages/CompanyListPage'
import CompanyPage from './pages/CompanyPage'
import PortfolioPage from './pages/PortfolioPage'
import ReportsPage from './pages/ReportsPage'
import ResearchPage from './pages/ResearchPage'
import './App.css'

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<Layout />}>
          <Route index element={<ResearchPage />} />
          <Route path="companies" element={<CompanyListPage />} />
          <Route path="companies/:ticker" element={<CompanyPage />} />
          <Route path="reports" element={<ReportsPage />} />
          <Route path="portfolio" element={<PortfolioPage />} />
          <Route path="*" element={<div className="container page">페이지를 찾을 수 없어요.</div>} />
        </Route>
      </Routes>
    </BrowserRouter>
  )
}
