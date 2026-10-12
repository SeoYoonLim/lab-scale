import { useEffect, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api } from '../api'
import { ApiError } from '../api/errors'
import type { RealtimePrice } from '../types'
import { formatDate, formatPct, formatPrice, trendClass } from '../utils/format'

// backend/API.md: 서버가 종목별로 3~5초 캐싱하므로 이 주기로 폴링해도 안전하다.
const REALTIME_POLL_MS = 3000

export default function CompanyPage() {
  const { ticker = '' } = useParams()
  // ticker가 바뀔 때 CompanyPageContent를 통째로 새로 마운트해서 realtime/notFound/error 등
  // 이전 종목의 상태가 리셋되게 한다 - effect 안에서 수동으로 setState(null/false) 하는 대신.
  return <CompanyPageContent key={ticker} ticker={ticker} />
}

type WatchlistState = 'idle' | 'adding' | 'added' | 'already' | 'error'

function CompanyPageContent({ ticker }: { ticker: string }) {
  const navigate = useNavigate()

  // 과거 주가 차트·뉴스·공시는 대응하는 백엔드 API가 없어서 mock으로 떠받치고 있었는데,
  // 포트폴리오 중심으로 가면서 뺐다. 이 화면은 이제 실시간 시세(진짜 데이터)만 보여주고,
  // 더 알고 싶으면 리서치 챗으로 보낸다.
  const [realtime, setRealtime] = useState<RealtimePrice | null>(null)
  const [notFound, setNotFound] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const [watchlistState, setWatchlistState] = useState<WatchlistState>('idle')
  const [watchlistError, setWatchlistError] = useState<string | null>(null)

  useEffect(() => {
    if (!ticker) return
    let cancelled = false

    async function poll() {
      try {
        const data = await api.getRealtimePrice(ticker)
        if (!cancelled) {
          setRealtime(data)
          setError(null)
        }
      } catch (err) {
        if (cancelled) return
        const message = err instanceof Error ? err.message : '시세를 가져오지 못했어요.'
        // company_resolver가 못 찾은 경우의 메시지 형식("'...' 종목을 찾지 못했습니다...")을 그대로 구분 기준으로 쓴다.
        if (message.includes('찾지 못했습니다')) {
          setNotFound(true)
        } else {
          setError(message)
        }
      }
    }

    void poll()
    const id = setInterval(poll, REALTIME_POLL_MS)
    return () => {
      cancelled = true
      clearInterval(id)
    }
  }, [ticker])

  function handleResearch() {
    const name = realtime?.company_name ?? ticker
    navigate('/', {
      state: { prefillQuestion: `${name} 최근 상황을 종합해서 알려줘. 주가, 뉴스, 공시를 같이 확인해줘.` },
    })
  }

  async function handleAddWatchlist() {
    setWatchlistState('adding')
    setWatchlistError(null)
    try {
      await api.addWatchlistItem(ticker)
      setWatchlistState('added')
    } catch (err) {
      // 서버는 이미 등록된 종목이면 409를 준다 - 에러로 보여주지 않고 "이미 관심종목이에요"로 안내한다.
      if (err instanceof ApiError && err.status === 409) {
        setWatchlistState('already')
        return
      }
      setWatchlistState('error')
      setWatchlistError(err instanceof Error ? err.message : '관심종목 추가에 실패했어요.')
    }
  }

  if (notFound) {
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
          <h1 className="company-title">{realtime?.company_name ?? ticker}</h1>
          <div className="meta muted">
            <span>{ticker}</span>
            {realtime?.corrected_from && <span>입력: {realtime.corrected_from}</span>}
          </div>
          <div className="watchlist-row">
            <button
              type="button"
              className="watchlist-button"
              onClick={handleAddWatchlist}
              disabled={watchlistState === 'adding' || watchlistState === 'added' || watchlistState === 'already'}
            >
              {watchlistState === 'added'
                ? '관심종목에 추가했어요'
                : watchlistState === 'already'
                  ? '이미 관심종목이에요'
                  : watchlistState === 'adding'
                    ? '추가 중…'
                    : '관심종목 추가'}
            </button>
            {watchlistState === 'error' && watchlistError && (
              <span className="notice-error small">{watchlistError}</span>
            )}
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
        ) : error ? (
          <p className="notice notice-error">{error}</p>
        ) : (
          <p className="muted">불러오는 중…</p>
        )}
      </section>

      <div className="card research-cta">
        <div>
          <h2>더 자세히 알고 싶으신가요?</h2>
          <p className="muted">주가, 뉴스, 공시를 종합한 AI 리서치를 받아보세요.</p>
        </div>
        <button type="button" className="send" onClick={handleResearch} disabled={!realtime && !error}>
          이 종목으로 리서치해줘
        </button>
      </div>
    </div>
  )
}
