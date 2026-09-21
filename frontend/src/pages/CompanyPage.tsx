import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api } from '../api'
import PriceChart from '../components/PriceChart'
import { useAsync } from '../hooks/useAsync'
import { formatDate, formatPct, formatPrice, formatVolume, trendClass } from '../utils/format'

const PERIODS = [5, 30, 90]

export default function CompanyPage() {
  const { ticker = '' } = useParams()
  const [days, setDays] = useState(30)

  const companies = useAsync(() => api.listCompanies(), [])
  const prices = useAsync(() => api.getPrices(ticker, days), [ticker, days])
  const news = useAsync(() => api.getNews(ticker), [ticker])
  const disclosures = useAsync(() => api.getDisclosures(ticker), [ticker])

  const company = companies.data?.find((c) => c.ticker === ticker)
  const latest = prices.data?.[prices.data.length - 1]

  if (companies.data && !company) {
    return (
      <div className="container page">
        <p className="notice">등록되지 않은 종목이에요.</p>
        <Link to="/companies" className="back-link">← 종목 목록</Link>
      </div>
    )
  }

  return (
    <div className="container page">
      <Link to="/companies" className="back-link">← 종목 목록</Link>

      <section className="card summary">
        <div>
          <h1 className="company-title">{company?.name ?? ticker}</h1>
          <div className="muted">
            {ticker}
            {company && ` · ${[company.market, company.sector].filter(Boolean).join(' · ')}`}
          </div>
        </div>
        {latest && (
          <div className="summary-price">
            <div className="price-big num">{formatPrice(latest.close_price)}원</div>
            <div className={`num ${trendClass(latest.change_pct)}`}>
              {formatPct(latest.change_pct)}
              <span className="muted"> · 거래량 {formatVolume(latest.volume)}주</span>
            </div>
            <div className="muted small">{formatDate(latest.price_date)} 종가 기준</div>
          </div>
        )}
      </section>

      <section className="card">
        <div className="card-head">
          <h2>주가</h2>
          <div className="segmented" role="group" aria-label="조회 기간">
            {PERIODS.map((period) => (
              <button
                key={period}
                type="button"
                className={period === days ? 'seg active' : 'seg'}
                onClick={() => setDays(period)}
              >
                {period}일
              </button>
            ))}
          </div>
        </div>
        {prices.error && <p className="notice notice-error">{prices.error.message}</p>}
        {prices.loading && !prices.data && <p className="muted">불러오는 중…</p>}
        {prices.data && prices.data.length === 0 && (
          <p className="muted">아직 수집된 주가 데이터가 없어요.</p>
        )}
        {prices.data && prices.data.length > 0 && <PriceChart data={prices.data} />}
      </section>

      <div className="two-col">
        <section className="card">
          <h2>최근 뉴스</h2>
          {news.error && <p className="notice notice-error">{news.error.message}</p>}
          {news.loading && !news.data && <p className="muted">불러오는 중…</p>}
          {news.data && news.data.length === 0 && <p className="muted">아직 수집된 뉴스가 없어요.</p>}
          <ul className="list">
            {news.data?.map((item, index) => (
              <li key={index}>
                <a href={item.url ?? undefined} target="_blank" rel="noreferrer">
                  {item.title}
                </a>
                <div className="muted small">
                  {item.source} · {formatDate(item.published_at)}
                </div>
              </li>
            ))}
          </ul>
        </section>

        <section className="card">
          <h2>최근 공시</h2>
          {disclosures.error && <p className="notice notice-error">{disclosures.error.message}</p>}
          {disclosures.loading && !disclosures.data && <p className="muted">불러오는 중…</p>}
          {disclosures.data && disclosures.data.length === 0 && (
            <p className="muted">아직 수집된 공시가 없어요.</p>
          )}
          <ul className="list">
            {disclosures.data?.map((item, index) => (
              <li key={index}>
                <a href={item.source_url ?? undefined} target="_blank" rel="noreferrer">
                  {item.title}
                </a>
                <div className="muted small">
                  DART · {formatDate(item.disclosed_at)}
                </div>
              </li>
            ))}
          </ul>
        </section>
      </div>
    </div>
  )
}
