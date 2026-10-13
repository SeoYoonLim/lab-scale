import { useEffect, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { api } from '../api'
import { useAsync } from '../hooks/useAsync'
import { formatPct, formatPrice, trendClass } from '../utils/format'

// GET /api/companies?q= 는 로그인 없이 바로 검색할 수 있다(별칭 포함, 종목명/티커 부분 일치).
// 예전처럼 300종목을 그냥 쭉 나열하는 대신, 검색으로 찾게 한다.
const SEARCH_DEBOUNCE_MS = 300

export default function CompanyListPage() {
  const [searchParams, setSearchParams] = useSearchParams()
  // ?q= 로 들어오면(공유한 링크, 종목 상세에서 뒤로가기) 검색어를 복원한다.
  const [q, setQ] = useState(() => searchParams.get('q') ?? '')
  const [debouncedQ, setDebouncedQ] = useState(q)

  useEffect(() => {
    const id = setTimeout(() => setDebouncedQ(q), SEARCH_DEBOUNCE_MS)
    return () => clearTimeout(id)
  }, [q])

  // 디바운스가 끝난 뒤에만 URL에 반영한다(replace라 history를 안 쌓는다) - 입력할 때마다 반영하면
  // 글자 하나 지울 때마다 URL이 바뀌어서 오히려 지저분해진다.
  useEffect(() => {
    setSearchParams(debouncedQ ? { q: debouncedQ } : {}, { replace: true })
  }, [debouncedQ, setSearchParams])

  const { data, error, loading, refetching } = useAsync(
    () => api.searchCompanies(debouncedQ || undefined, 50),
    [debouncedQ],
  )

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
      {/* 검색어를 바꾸는 중에는 이전 결과를 그대로 보여주면서(깜빡임 없이) 이 표시만 띄운다 -
          안 그러면 느린 검색일 때 이전 검색어의 결과가 새 검색어 결과처럼 잠깐 보일 수 있다. */}
      {refetching && <p className="muted small">검색 중…</p>}
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
