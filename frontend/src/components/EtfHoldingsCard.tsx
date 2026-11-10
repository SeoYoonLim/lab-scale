import { useQuery } from '@tanstack/react-query'
import { api } from '../api'
import { queryKeys } from '../api/queryKeys'
import { formatPlainPct, formatTradingDate } from '../utils/format'
import Banner from './ui/Banner'
import ErrorNotice from './ui/ErrorNotice'

const LIMIT = 10

// ETF 구성종목·편입 비중(GET /api/etf/{ticker}/holdings, 운용사 공시). KODEX·TIGER 일부만 수집하고 나머지는 "수집하지 않았습니다"
// (200 + items: [])라서 오류가 아니라 안내 상태로 보여준다. 비중 합계가 100%에서 조금 벗어나는 건 정상이다.
export default function EtfHoldingsCard({ ticker }: { ticker: string }) {
  const { data, error, isPending, refetch } = useQuery({
    queryKey: queryKeys.etfHoldings(ticker, LIMIT),
    queryFn: () => api.getEtfHoldings(ticker, LIMIT),
    retry: false,
  })

  return (
    <section className="card etf-holdings-card" aria-labelledby="etf-holdings-heading">
      <h2 id="etf-holdings-heading">구성종목·편입 비중</h2>
      {isPending && <p className="muted">불러오는 중…</p>}
      {error && <ErrorNotice error={error} onRetry={() => void refetch()} />}
      {data && (
        <>
          {data.stale && data.stale_notice && <Banner tone="warn">{data.stale_notice}</Banner>}
          {data.items.length === 0 ? (
            <p className="muted">{data.message ?? '구성종목 데이터가 없어요.'}</p>
          ) : (
            <>
              <p className="muted small">
                기준일 <span className="num">{formatTradingDate(data.as_of_date)}</span> · 전체 {data.holding_count}종목 · 비중 합계{' '}
                <span className="num">{formatPlainPct(data.total_weight_pct)}</span>
                {data.source && <> · 출처: {data.source}</>}
              </p>
              <div className="table-scroll">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th scope="col">순위</th>
                      <th scope="col">구성종목</th>
                      <th scope="col">비중</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.items.map((item) => (
                      <tr key={`${item.rank}-${item.name}`}>
                        <td className="num">{item.rank}</td>
                        <th scope="row">
                          {item.name}
                          {item.code && <span className="muted small num"> {item.code}</span>}
                        </th>
                        <td className="num">{formatPlainPct(item.weight_pct)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <p className="muted small">상위 {data.items.length}개 합계 {formatPlainPct(data.top_weight_pct)}. 운용사 공시 기준이며 현재 구성과 다를 수 있어요.</p>
            </>
          )}
          {data.data_warnings.map((w, i) => (
            <p key={`${w.kind}-${i}`} className="muted small">
              {w.message}
            </p>
          ))}
        </>
      )}
    </section>
  )
}
