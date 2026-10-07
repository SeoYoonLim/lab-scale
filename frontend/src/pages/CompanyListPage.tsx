import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api'
import { useAsync } from '../hooks/useAsync'
import { formatPct, formatPrice, trendClass } from '../utils/format'

// GET /api/companies?q= 는 로그인 없이 바로 검색할 수 있다(별칭 포함, 종목명/티커 부분 일치).
// 예전처럼 300종목을 그냥 쭉 나열하는 대신, 검색으로 찾게 한다.
const SEARCH_DEBOUNCE_MS = 300

export default function CompanyListPage() {
  const [q, setQ] = useState('')
  const [debouncedQ, setDebouncedQ] = useState('')

  useEffect(() => {
    const id = setTimeout(() => setDebouncedQ(q), SEARCH_DEBOUNCE_MS)
    return () => clearTimeout(id)
  }, [q])

  const { data, error, loading } = useAsync(() => api.searchCompanies(debouncedQ || undefined, 50), [debouncedQ])

  return (
    <div className="container page">
      <h1 className="page-title">종목</h1>

      <input
        className="company-search"
        value={q}
        onChange={(e) => setQ(e.target.value)}
        placeholder="종목명 또는 종목코드로 검색 (예: 삼성전자)"
        aria-label="종목 검색"
      />

      {loading && !data && <p className="muted">불러오는 중…</p>}
      {error && <p className="notice notice-error">{error.message}</p>}
      {data && data.length === 0 && <p className="muted">검색 결과가 없어요.</p>}

      {data && data.length > 0 && (
        <div className="card">
          <div className="company-grid">
            {data.map((company) => (
              <Link key={company.ticker} to={`/companies/${company.ticker}`} className="company-card">
                <div>
                  <div className="company-name">{company.name}</div>
                  <div className="meta muted">
                    <span>{company.ticker}</span>
                    {company.market && <span>{company.market}</span>}
                  </div>
                </div>
                {company.latest_close != null ? (
                  <div className="company-price">
                    <div className="num">{formatPrice(company.latest_close)}원</div>
                    <div className={`num ${trendClass(company.change_pct)}`}>{formatPct(company.change_pct)}</div>
                  </div>
                ) : (
                  <div className="company-price muted">종가 없음</div>
                )}
              </Link>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
