import { Link } from 'react-router-dom'
import { api } from '../api'
import { useAsync } from '../hooks/useAsync'
import type { Company } from '../types'
import { formatPct, formatPrice, trendClass } from '../utils/format'

function CompanyCard({ company }: { company: Company }) {
  const { data } = useAsync(() => api.getPrices(company.ticker, 2), [company.ticker])
  const latest = data?.[data.length - 1]

  return (
    <Link to={`/companies/${company.ticker}`} className="card company-card">
      <div>
        <div className="company-name">{company.name}</div>
        <div className="muted">
          {company.ticker} · {[company.market, company.sector].filter(Boolean).join(' · ')}
        </div>
      </div>
      {latest ? (
        <div className="company-price">
          <div className="num">{formatPrice(latest.close_price)}원</div>
          <div className={`num ${trendClass(latest.change_pct)}`}>{formatPct(latest.change_pct)}</div>
        </div>
      ) : (
        <div className="company-price muted">-</div>
      )}
    </Link>
  )
}

export default function CompanyListPage() {
  const { data, error, loading } = useAsync(() => api.listCompanies(), [])

  return (
    <div className="container page">
      <h1 className="page-title">종목</h1>
      {loading && !data && <p className="muted">불러오는 중…</p>}
      {error && <p className="notice notice-error">{error.message}</p>}
      {data && data.length === 0 && <p className="muted">등록된 종목이 없어요.</p>}
      <div className="company-grid">
        {data?.map((company) => <CompanyCard key={company.ticker} company={company} />)}
      </div>
    </div>
  )
}
