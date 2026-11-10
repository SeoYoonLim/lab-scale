import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../../api'
import {
  BENCHMARK_CODES,
  PERFORMANCE_PERIODS,
  hasBenchmarkChart,
  hasPerformanceNumbers,
} from '../../api/contracts/performance'
import type { BenchmarkCode, PerformancePeriod, PerformanceView } from '../../api/contracts/performance'
import { queryKeys } from '../../api/queryKeys'
import LineChart from '../../components/LineChart'
import Banner from '../../components/ui/Banner'
import EmptyState from '../../components/ui/EmptyState'
import ErrorNotice from '../../components/ui/ErrorNotice'
import StatusBadge from '../../components/ui/StatusBadge'
import { useLocale } from '../../i18n/useLocale'
import { formatPlainPct, formatSignedPct, formatSignedPp, formatTradingDate, formatWon } from '../../utils/format'
import { trendClass } from '../../utils/format'

type Mode = 'index' | 'amount'

function SummaryCards({ view }: { view: PerformanceView }) {
  const { t } = useLocale()
  const s = view.summary
  return (
    <div className="stat-grid">
      <div className="stat-card">
        <span className="muted small">{t('performance.total_asset')}</span>
        <span className="stat-value num">{formatWon(s.end_value)}</span>
      </div>
      <div className="stat-card">
        <span className="muted small">{t('performance.start_value')}</span>
        <span className="stat-value num">{formatWon(s.start_value)}</span>
      </div>
      <div className="stat-card">
        <span className="muted small">{t('performance.profit_loss')}</span>
        <span className={`stat-value num ${trendClass(s.profit_loss)}`}>{formatWon(s.profit_loss)}</span>
      </div>
      <div className="stat-card">
        <span className="muted small">{t('performance.period_return')}</span>
        <span className={`stat-value num ${trendClass(s.period_return_pct)}`}>{formatSignedPct(s.period_return_pct)}</span>
      </div>
      <div className="stat-card">
        <span className="muted small">{t('performance.cumulative_return')}</span>
        <span className={`stat-value num ${trendClass(s.cumulative_return_pct)}`}>{formatSignedPct(s.cumulative_return_pct)}</span>
      </div>
    </div>
  )
}

function ComparisonCard({ view }: { view: PerformanceView }) {
  const { t } = useLocale()
  const c = view.comparison
  // 비교는 comparison.status가 ok·partial일 때만 숫자를 보여준다. 아니면 서버가 준 안내 문장만(핸드오프 3-3절).
  if (!hasBenchmarkChart(c.status)) {
    return (
      <div className="notice">
        <strong>{t(`performance.benchmark_status.${c.status}`)}</strong>
        {c.message && <p className="muted small">{c.message}</p>}
      </div>
    )
  }
  return (
    <div className="stat-grid compare-grid">
      <div className="stat-card">
        <span className="muted small">{t('performance.portfolio')}</span>
        <span className={`stat-value num ${trendClass(c.portfolio_return_pct ?? null)}`}>{formatSignedPct(c.portfolio_return_pct)}</span>
      </div>
      <div className="stat-card">
        <span className="muted small">
          {t('performance.benchmark_return')} ({c.benchmark_name})
        </span>
        <span className={`stat-value num ${trendClass(c.benchmark_return_pct ?? null)}`}>{formatSignedPct(c.benchmark_return_pct)}</span>
      </div>
      <div className="stat-card">
        <span className="muted small">
          {t('performance.excess_return')}{' '}
          {c.status === 'partial' && <StatusBadge tone="warn">{t('performance.reference_only', { fallback: '참고용' })}</StatusBadge>}
        </span>
        <span className={`stat-value num ${trendClass(c.excess_return_pp ?? null)}`}>{formatSignedPp(c.excess_return_pp)}</span>
      </div>
    </div>
  )
}

function PerformanceChart({ view }: { view: PerformanceView }) {
  const { t } = useLocale()
  const [mode, setMode] = useState<Mode>('index')
  const points = view.series.points
  const cmp = view.comparison
  const showIndex = hasBenchmarkChart(cmp.status) && cmp.points.length > 1

  // 정규화 지수(시작=100)와 금액(원)은 축이 달라서 한 그래프에 섞지 않고 전환한다(핸드오프 8절 4번).
  const effectiveMode: Mode = mode === 'index' && !showIndex ? 'amount' : mode
  const dates = effectiveMode === 'index' ? cmp.points.map((p) => p.date) : points.map((p) => p.date)
  const estimatedByDate = new Set(points.filter((p) => p.estimated).map((p) => p.date))
  const estimated = dates.map((d) => estimatedByDate.has(d))

  const series =
    effectiveMode === 'index'
      ? [
          { key: 'portfolio', label: t('performance.portfolio'), color: 'var(--accent)', values: cmp.points.map((p) => p.portfolio_index), estimated },
          { key: 'benchmark', label: cmp.benchmark_name, color: 'var(--ink-muted)', values: cmp.points.map((p) => p.benchmark_index) },
        ]
      : [{ key: 'asset', label: t('performance.total_asset'), color: 'var(--accent)', values: points.map((p) => p.total_asset), estimated }]

  return (
    <div className="performance-chart">
      <div className="card-head">
        <h3 className="sub-title">
          {effectiveMode === 'index' ? t('performance.chart_title', { fallback: '내 포트폴리오 vs 지수(시작=100)' }) : t('performance.total_asset')}
        </h3>
        {showIndex && (
          <div className="segmented" role="group" aria-label={t('performance.chart_title', { fallback: '내 포트폴리오 vs 지수(시작=100)' })}>
            <button type="button" className={effectiveMode === 'index' ? 'seg active' : 'seg'} aria-pressed={effectiveMode === 'index'} onClick={() => setMode('index')}>
              {t('performance.benchmark', { fallback: '지수 비교' })}
            </button>
            <button type="button" className={effectiveMode === 'amount' ? 'seg active' : 'seg'} aria-pressed={effectiveMode === 'amount'} onClick={() => setMode('amount')}>
              {t('performance.total_asset')}
            </button>
          </div>
        )}
      </div>
      <LineChart
        dates={dates}
        series={series}
        baseline={effectiveMode === 'index' ? 100 : null}
        formatValue={(v) => (effectiveMode === 'index' ? v.toFixed(1) : Math.round(v).toLocaleString('ko-KR'))}
        ariaLabel={`${t('performance.title')}: ${formatTradingDate(dates[0])} → ${formatTradingDate(dates.at(-1))}`}
        readout={(index) => {
          const i = index ?? dates.length - 1
          if (effectiveMode === 'index') {
            const p = cmp.points[i]
            if (!p) return null
            return (
              <>
                <strong className="num">{formatTradingDate(p.date)}</strong>
                <span className={`num ${trendClass(p.portfolio_return_pct)}`}>
                  {t('performance.portfolio')} {formatSignedPct(p.portfolio_return_pct)}
                </span>
                <span className={`num ${trendClass(p.benchmark_return_pct)}`}>
                  {cmp.benchmark_name} {formatSignedPct(p.benchmark_return_pct)}
                </span>
                <span className={`num ${trendClass(p.excess_return_pp)}`}>
                  {t('performance.excess_return')} {formatSignedPp(p.excess_return_pp)}
                </span>
                {p.estimated && <StatusBadge tone="warn">{t('performance.estimated_badge')}</StatusBadge>}
              </>
            )
          }
          const p = points[i]
          if (!p) return null
          return (
            <>
              <strong className="num">{formatTradingDate(p.date)}</strong>
              <span className="num">{formatWon(p.total_asset)}</span>
              <span className="num muted">
                {t('exposure.cash', { fallback: '현금' })} {formatWon(p.cash_balance)}
              </span>
              <span className={`num ${trendClass(p.daily_return_pct)}`}>{formatSignedPct(p.daily_return_pct)}</span>
              {p.estimated && <StatusBadge tone="warn">{t('performance.estimated_badge')}</StatusBadge>}
            </>
          )
        }}
      />
      {view.series.sampled && (
        <p className="muted small">
          {view.series.total_points}개 거래일 중 {view.series.points.length}개만 그렸어요(첫·끝·최고·최저·최대 낙폭 지점은 남겼어요).
        </p>
      )}
    </div>
  )
}

function MetricsBlock({ view }: { view: PerformanceView }) {
  const { t } = useLocale()
  const m = view.metrics
  const na = t('performance.not_enough_data')
  const dd = m.max_drawdown
  return (
    <dl className="metric-list">
      <div>
        <dt>{t('performance.highest')}</dt>
        <dd className="num">{m.highest ? `${formatWon(m.highest.value)} (${formatTradingDate(m.highest.date)})` : na}</dd>
      </div>
      <div>
        <dt>{t('performance.lowest')}</dt>
        <dd className="num">{m.lowest ? `${formatWon(m.lowest.value)} (${formatTradingDate(m.lowest.date)})` : na}</dd>
      </div>
      <div>
        <dt>{t('performance.max_drawdown')}</dt>
        <dd className="num">
          {dd
            ? `${formatSignedPct(dd.pct)} (${formatTradingDate(dd.peak_date)} → ${formatTradingDate(dd.trough_date)}${dd.recovery_date ? ` · ${formatTradingDate(dd.recovery_date)}` : ''})`
            : na}
        </dd>
      </div>
      <div>
        <dt>{t('performance.volatility')}</dt>
        <dd className="num">{m.daily_volatility_pct != null ? formatPlainPct(m.daily_volatility_pct) : na}</dd>
      </div>
      <div>
        <dt>{t('performance.annualized_return')}</dt>
        <dd className="num">{m.annualized_return_pct != null ? formatSignedPct(m.annualized_return_pct) : na}</dd>
      </div>
    </dl>
  )
}

export default function PerformanceSection() {
  const { t, locale } = useLocale()
  const [period, setPeriod] = useState<PerformancePeriod>('3m')
  const [benchmark, setBenchmark] = useState<BenchmarkCode>('KS11')

  const { data, error, isPending, isPlaceholderData, refetch } = useQuery({
    queryKey: queryKeys.performance(period, benchmark, locale),
    queryFn: () => api.getPerformance({ period, benchmark }),
    placeholderData: keepPreviousData,
  })

  const delayed = data?.warnings.find((w) => w.kind === 'data_delayed')
  const otherWarnings = data?.warnings.filter((w) => w.kind !== 'data_delayed') ?? []
  const numbers = data != null && hasPerformanceNumbers(data.status)

  return (
    <section aria-labelledby="performance-heading">
      <h2 className="section-title" id="performance-heading">
        {t('performance.title')}
      </h2>
      <div className="card">
        <div className="performance-controls">
          <div className="segmented" role="group" aria-label={t('performance.period_label', { fallback: '기간' })}>
            {PERFORMANCE_PERIODS.map((p) => (
              <button key={p} type="button" className={p === period ? 'seg active' : 'seg'} aria-pressed={p === period} onClick={() => setPeriod(p)}>
                {t(`performance.period.${p}`)}
              </button>
            ))}
          </div>
          <label className="inline-select">
            <span className="muted small">{t('performance.benchmark')}</span>
            <select value={benchmark} onChange={(e) => setBenchmark(e.target.value as BenchmarkCode)}>
              {BENCHMARK_CODES.map((code) => (
                <option key={code} value={code}>
                  {t(`performance.index.${code}`)}
                </option>
              ))}
            </select>
          </label>
        </div>

        {isPending && <p className="muted">{t('common.loading')}</p>}
        {error && <ErrorNotice error={error} onRetry={() => void refetch()} />}

        {data && (
          <div className={isPlaceholderData ? 'is-stale' : undefined} aria-busy={isPlaceholderData}>
            {/* 데이터가 늦으면 맨 위에 먼저 알린다(핸드오프 7절) */}
            {delayed && <Banner tone="warn" title={t('common.stale_title', { fallback: '데이터가 최신이 아니에요' })}>{delayed.message}</Banner>}

            <p className="muted small data-basis-line">
              <span>
                {t('performance.data_date')} <span className="num">{formatTradingDate(data.data_date)}</span>
              </span>
              <StatusBadge tone={data.status === 'ok' ? 'ok' : data.status === 'partial' ? 'warn' : 'neutral'}>
                {t(`performance.status.${data.status}`)}
              </StatusBadge>
              {data.status === 'partial' && <span>{t('performance.partial_note', { fallback: '일부 날짜는 추정값이에요.' })}</span>}
            </p>

            {!numbers ? (
              // ok·partial이 아니면 수익률은 null이다. 0%나 빈 그래프를 그리지 않고 서버 안내(message)만 보여준다.
              <EmptyState
                message={data.message ?? t(`performance.status.${data.status}`)}
                action={
                  data.status === 'no_trades' || data.status === 'no_account' ? (
                    <Link to="/portfolio" className="watchlist-button">
                      {t('performance.empty_cta', { fallback: '모의투자 하러 가기' })}
                    </Link>
                  ) : undefined
                }
              />
            ) : (
              <>
                <SummaryCards view={data} />
                <ComparisonCard view={data} />
                <PerformanceChart view={data} />
                <h3 className="sub-title">{t('performance.metrics_title', { fallback: '위험·범위 지표' })}</h3>
                <MetricsBlock view={data} />
              </>
            )}

            {otherWarnings.length > 0 && (
              <div className="warning-list">
                <h3 className="sub-title">{t('performance.warnings_title', { fallback: '참고' })}</h3>
                <ul>
                  {otherWarnings.map((w, i) => (
                    <li key={`${w.kind}-${i}`}>{w.message}</li>
                  ))}
                </ul>
              </div>
            )}
            <p className="disclaimer-note muted small">{data.disclaimer}</p>
          </div>
        )}
      </div>
    </section>
  )
}
