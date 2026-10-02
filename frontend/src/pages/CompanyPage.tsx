import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api } from '../api'
import PriceChart from '../components/PriceChart'
import { useAsync } from '../hooks/useAsync'
import type { RealtimePrice } from '../types'
import { formatDate, formatPct, formatPrice, formatVolume, trendClass } from '../utils/format'

const PERIODS = [5, 30, 90]
// backend/API.md: 서버가 종목별로 3~5초 캐싱하므로 이 주기로 폴링해도 안전하다.
const REALTIME_POLL_MS = 3000

export default function CompanyPage() {
  const { ticker = '' } = useParams()
  const [days, setDays] = useState(30)

  const companies = useAsync(() => api.listCompanies(), [])
  const prices = useAsync(() => api.getPrices(ticker, days), [ticker, days])
  const news = useAsync(() => api.getNews(ticker), [ticker])
  const disclosures = useAsync(() => api.getDisclosures(ticker), [ticker])

  const company = companies.data?.find((c) => c.ticker === ticker)
  const latest = prices.data?.[prices.data.length - 1]

  // 실시간 시세는 과거 주가(mock)와 별도로, 실제 백엔드(/api/stocks/{ticker}/realtime-price)를 짧은 주기로 폴링한다.
  const [realtime, setRealtime] = useState<RealtimePrice | null>(null)
  const [realtimeError, setRealtimeError] = useState<string | null>(null)

  useEffect(() => {
    if (!ticker) return
    let cancelled = false

    async function poll() {
      try {
        const data = await api.getRealtimePrice(ticker)
        if (!cancelled) {
          setRealtime(data)
          setRealtimeError(null)
        }
      } catch (err) {
        if (!cancelled) setRealtimeError(err instanceof Error ? err.message : '시세를 가져오지 못했어요.')
      }
    }

    void poll()
    const id = setInterval(poll, REALTIME_POLL_MS)
    return () => {
      cancelled = true
      clearInterval(id)
    }
  }, [ticker])

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
          <div className="meta muted">
            <span>{ticker}</span>
            {company?.market && <span>{company.market}</span>}
            {company?.sector && <span>{company.sector}</span>}
          </div>
        </div>
        {realtime ? (
          <div className="summary-price">
            <div className="price-big num">{formatPrice(realtime.current_price)}원</div>
            <div className={`meta meta-end ${trendClass(realtime.change_pct)}`}>
              <span className="num">{formatPct(realtime.change_pct)}</span>
              {realtime.change_amount != null && (
                <span className="num">
                  {realtime.change_amount > 0 ? '+' : ''}
                  {formatPrice(realtime.change_amount)}원
                </span>
              )}
            </div>
            <div className="muted small">
              {realtime.is_realtime ? '실시간 시세' : `${formatDate(realtime.as_of)} 종가 기준`}
            </div>
          </div>
        ) : (
          realtimeError &&
          latest && (
            <div className="summary-price">
              <div className="price-big num">{formatPrice(latest.close_price)}원</div>
              <div className={`meta meta-end ${trendClass(latest.change_pct)}`}>
                <span className="num">{formatPct(latest.change_pct)}</span>
                <span className="muted">거래량 {formatVolume(latest.volume)}주</span>
              </div>
              <div className="muted small">{formatDate(latest.price_date)} 종가 기준(실시간 시세 불러오기 실패)</div>
            </div>
          )
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
                <div className="meta muted small">
                  <span>{item.source}</span>
                  <span>{formatDate(item.published_at)}</span>
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
                <div className="meta muted small">
                  <span>DART</span>
                  <span>{formatDate(item.disclosed_at)}</span>
                </div>
              </li>
            ))}
          </ul>
        </section>
      </div>
    </div>
  )
}
