import { useState } from 'react'
import { exposureBarSegments } from '../api/contracts/exposure'
import type {
  CompanyExposure,
  EtfContribution,
  ExposureDataWarning,
  ExposureFlag,
  LookThroughSummary,
  SectorExposure,
  UnpricedHolding,
} from '../api/contracts/exposure'
import { useLocale } from '../i18n/useLocale'
import { formatPlainPct, formatTradingDate, formatWon } from '../utils/format'
import Banner from './ui/Banner'
import StatusBadge from './ui/StatusBadge'

export interface LookThroughBody {
  summary: LookThroughSummary
  companies: CompanyExposure[]
  overlaps: CompanyExposure[]
  etfs: EtfContribution[]
  unpriced: UnpricedHolding[]
  sector: SectorExposure
  flags: ExposureFlag[]
  data_warnings: ExposureDataWarning[]
}

// 데이터 품질 접기에 넣는 경고 종류(핸드오프 4절 표의 "데이터 품질 접기"). 나머지는 ETF 카드·본문에 바로 보인다.
const QUALITY_KINDS = new Set(['etf_negative_weights', 'etf_null_weights', 'etf_duplicate_constituents', 'etf_unlinked_constituents'])

function SummaryBar({ summary }: { summary: LookThroughSummary }) {
  const { t } = useLocale()
  const segments = exposureBarSegments(summary)
  const label: Record<string, string> = {
    companies: t('exposure.companies', { fallback: '기업 노출(직접+ETF)' }),
    unidentified: t('look_through.unidentified'),
    cash: t('exposure.cash', { fallback: '현금' }),
  }
  // 합이 100%를 조금 넘을 수 있어(구성 비중 초과) 너비는 합계 대비로 맞춘다
  const sum = segments.reduce((s, seg) => s + seg.weightPct, 0) || 1
  return (
    <div className="exposure-bar-block">
      <h3 className="sub-title">{t('exposure.bar_title', { fallback: '총자산 대비 구성' })}</h3>
      <div className="exposure-bar" role="img" aria-label={segments.map((s) => `${label[s.key]} ${formatPlainPct(s.weightPct)}`).join(', ')}>
        {segments.map((seg) => (
          <span key={seg.key} className={`exposure-seg exposure-seg-${seg.key}`} style={{ width: `${(seg.weightPct / sum) * 100}%` }} />
        ))}
      </div>
      <ul className="exposure-legend">
        {segments.map((seg) => (
          <li key={seg.key}>
            <span className={`legend-swatch exposure-seg-${seg.key}`} aria-hidden="true" />
            {label[seg.key]} <span className="num">{formatPlainPct(seg.weightPct)}</span>
          </li>
        ))}
      </ul>
      <p className="muted small">
        {t('look_through.total_asset')} <span className="num">{formatWon(summary.total_asset)}</span>
        {summary.unidentified_amount > 0 && (
          <>
            {' · '}
            {t('look_through.unidentified')} <span className="num">{formatWon(summary.unidentified_amount)}</span>
          </>
        )}
      </p>
    </div>
  )
}

function Top1Compare({ summary }: { summary: LookThroughSummary }) {
  const { t } = useLocale()
  const lt = summary.look_through_top1
  // 직접 보유 집중도와 실질 집중도를 나란히 보여준다(핸드오프 6-1절). 둘 다 총자산 대비다.
  return (
    <div className="stat-grid">
      <div className="stat-card">
        <span className="muted small">{t('exposure.direct_top1', { fallback: '직접 보유 1위 비중' })}</span>
        <span className="stat-value num">{formatPlainPct(summary.direct_top1_weight_pct)}</span>
      </div>
      <div className="stat-card">
        <span className="muted small">{t('exposure.look_through_top1', { fallback: '실질 노출 1위' })}</span>
        <span className="stat-value num">{lt ? `${lt.name} ${formatPlainPct(lt.total_weight_pct)}` : '—'}</span>
      </div>
    </div>
  )
}

function CompanyRow({ company, expandable }: { company: CompanyExposure; expandable: boolean }) {
  const { t } = useLocale()
  const [open, setOpen] = useState(false)
  const hasVia = company.via_etfs.length > 0
  return (
    <>
      <tr>
        <th scope="row">
          <span className="rank num">{company.rank}</span> {company.name}{' '}
          {!company.linked && <StatusBadge>{t('look_through.unlinked')}</StatusBadge>}
        </th>
        <td>
          <StatusBadge tone={company.exposure_type === 'both' ? 'accent' : 'neutral'}>
            {t(`look_through.exposure_type.${company.exposure_type}`)}
          </StatusBadge>
        </td>
        <td className="num">{formatPlainPct(company.direct_weight_pct)}</td>
        <td className="num">{formatPlainPct(company.via_etf_weight_pct)}</td>
        <td className="num strong">{formatPlainPct(company.total_weight_pct)}</td>
        {expandable && (
          <td>
            {hasVia && (
              <button type="button" className="link-button" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
                {open ? t('exposure.hide_via', { fallback: 'ETF 접기' }) : t('exposure.show_via', { fallback: 'ETF 보기' })}
              </button>
            )}
          </td>
        )}
      </tr>
      {open && hasVia && (
        <tr className="via-row">
          <td colSpan={6}>
            <ul className="via-list">
              {company.via_etfs.map((v) => (
                <li key={`${v.ticker}-${v.name}`}>
                  {v.name}: {t('look_through.constituent_weight')} <span className="num">{formatPlainPct(v.constituent_weight_pct)}</span> →{' '}
                  {t('look_through.contribution')} <span className="num">{formatWon(v.amount)}</span> ({formatPlainPct(v.asset_weight_pct)})
                </li>
              ))}
            </ul>
          </td>
        </tr>
      )}
    </>
  )
}

function CompanyTable({ companies, expandable }: { companies: CompanyExposure[]; expandable: boolean }) {
  const { t } = useLocale()
  return (
    <div className="table-scroll">
      <table className="data-table exposure-table">
        <thead>
          <tr>
            <th scope="col">{t('look_through.company')}</th>
            <th scope="col">{t('exposure.type', { fallback: '구분' })}</th>
            <th scope="col">{t('look_through.direct')}</th>
            <th scope="col">{t('look_through.via_etf')}</th>
            <th scope="col">{t('look_through.total_weight')}</th>
            {expandable && <th scope="col" />}
          </tr>
        </thead>
        <tbody>
          {companies.map((c) => (
            <CompanyRow key={`${c.code}-${c.name}`} company={c} expandable={expandable} />
          ))}
        </tbody>
      </table>
    </div>
  )
}

function EtfCard({ etf, warnings }: { etf: EtfContribution; warnings: ExposureDataWarning[] }) {
  const { t } = useLocale()
  const own = warnings.filter((w) => w.ticker === etf.ticker)
  const quality = own.filter((w) => QUALITY_KINDS.has(w.kind))
  const notices = own.filter((w) => !QUALITY_KINDS.has(w.kind) && w.kind !== 'etf_holdings_stale')
  return (
    <article className="etf-card">
      <header className="etf-card-head">
        <div>
          <strong>{etf.name}</strong> <span className="muted small num">{etf.ticker}</span>
        </div>
        <div className="etf-card-badges">
          <StatusBadge tone={etf.status === 'decomposed' ? 'ok' : 'warn'}>{t(`look_through.etf_status.${etf.status}`)}</StatusBadge>
          {etf.leverage_kind && <StatusBadge tone="danger">{t('look_through.leverage_badge')}</StatusBadge>}
          {etf.holdings_stale && <StatusBadge tone="warn">{t('look_through.stale_badge')}</StatusBadge>}
        </div>
      </header>
      <p className="muted small">
        {t('look_through.eval_amount')} <span className="num">{formatWon(etf.eval_amount)}</span> ({formatPlainPct(etf.asset_weight_pct)}
        {' · '}
        {t('look_through.holdings_as_of')} <span className="num">{formatTradingDate(etf.holdings_as_of)}</span>)
      </p>
      {etf.reason_code && <p className="small">{t(`look_through.reason.${etf.reason_code}`, { fallback: etf.reason_code })}</p>}
      {/* 레버리지·인버스: 공시 구성 비중이며 경제적 노출이 아니다(핸드오프 3-8절). 서버 문장을 그대로 보여준다. */}
      {etf.notice && <Banner tone="warn">{etf.notice}</Banner>}
      {notices.map((w, i) => (
        <p key={`${w.kind}-${i}`} className="muted small">
          {w.message}
        </p>
      ))}
      {etf.constituents.length > 0 && (
        <div className="table-scroll">
          <table className="data-table">
            <caption className="muted small">{t('look_through.constituents')}</caption>
            <thead>
              <tr>
                <th scope="col">{t('look_through.company')}</th>
                <th scope="col">{t('look_through.constituent_weight')}</th>
                <th scope="col">{t('look_through.contribution')}</th>
              </tr>
            </thead>
            <tbody>
              {etf.constituents.map((c) => (
                <tr key={`${c.code}-${c.name}`}>
                  <th scope="row">
                    {c.name} {c.kind === 'nested_etf' && <StatusBadge>{t('look_through.nested_etf')}</StatusBadge>}
                    {!c.linked_ticker && c.kind !== 'nested_etf' && <StatusBadge>{t('look_through.unlinked')}</StatusBadge>}
                  </th>
                  <td className="num">{formatPlainPct(c.weight_pct)}</td>
                  {/* 분해하지 않은 ETF는 금액이 null이다(공시 비중만) - 0원이 아니라 "—" */}
                  <td className="num">{formatWon(c.amount)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {etf.residual_amount != null && etf.status === 'decomposed' && (
        <p className="muted small">
          {t('look_through.residual')} <span className="num">{formatWon(etf.residual_amount)}</span>
          {etf.nested_etf_amount ? (
            <>
              {' · '}
              {t('look_through.nested_etf')} <span className="num">{formatWon(etf.nested_etf_amount)}</span>
            </>
          ) : null}
        </p>
      )}
      {quality.length > 0 && (
        <details className="quality-details">
          <summary>{t('look_through.data_quality')}</summary>
          <ul>
            {quality.map((w, i) => (
              <li key={`${w.kind}-${i}`}>{w.message}</li>
            ))}
          </ul>
        </details>
      )}
    </article>
  )
}

// ETF 실질 노출 본문. 노출 화면(GET /portfolio/exposure)과 진단 결과의 look_through 스냅샷이 같은 모양이라 같이 쓴다.
export default function LookThroughPanels({ body, compact = false }: { body: LookThroughBody; compact?: boolean }) {
  const { t } = useLocale()
  const { summary } = body
  const unpricedKinds = body.data_warnings.filter((w) => w.kind === 'older_price')
  // etf_* 경고 중 ETF 카드 안에서 보여주는 것은 제외하고(ETF 카드가 자기 ticker 경고를 쓴다), 종목에 안 묶이는 경고만 따로 보인다.
  const loose = body.data_warnings.filter(
    (w) => w.kind !== 'older_price' && !w.kind.startsWith('etf_') && w.kind !== 'no_price',
  )

  return (
    <div className="look-through">
      <SummaryBar summary={summary} />
      <Top1Compare summary={summary} />

      {body.flags.length > 0 && (
        <div className="flag-list">
          <h3 className="sub-title">{t('exposure.flags', { fallback: '집중 경고' })}</h3>
          <ul>
            {body.flags.map((f, i) => (
              <li key={`${f.kind}-${i}`}>
                <StatusBadge tone="warn">{t(`exposure_flag.${f.kind}`, { fallback: f.kind })}</StatusBadge> {f.message}
              </li>
            ))}
          </ul>
        </div>
      )}

      {body.companies.length > 0 ? (
        <>
          <h3 className="sub-title">{t('look_through.company')}</h3>
          <CompanyTable companies={body.companies} expandable={!compact} />
        </>
      ) : (
        <p className="muted">{t('exposure.no_companies', { fallback: '기업별로 나눌 수 있는 ETF가 없어 미확인 노출만 보여드려요.' })}</p>
      )}

      {!compact && body.overlaps.length > 0 && (
        <>
          <h3 className="sub-title">{t('exposure.overlaps', { fallback: '직접 + ETF로 겹쳐 가진 기업' })}</h3>
          <CompanyTable companies={body.overlaps} expandable={false} />
        </>
      )}

      {body.etfs.length > 0 && (
        <>
          <h3 className="sub-title">{t('exposure.etf_cards', { fallback: 'ETF별 구성' })}</h3>
          <div className="etf-card-list">
            {body.etfs.map((etf) => (
              <EtfCard key={etf.ticker} etf={etf} warnings={body.data_warnings} />
            ))}
          </div>
        </>
      )}

      {body.sector.status === 'unavailable' ? (
        <p className="muted small">{body.sector.message}</p>
      ) : (
        <>
          <h3 className="sub-title">{t(`look_through.sector_status.${body.sector.status}`)}</h3>
          <ul className="plain-list">
            {body.sector.items.map((item) => (
              <li key={item.sector}>
                {item.sector} <span className="num">{formatWon(item.amount)}</span>
              </li>
            ))}
          </ul>
        </>
      )}

      {body.unpriced.length > 0 && (
        <div className="warning-list">
          <h3 className="sub-title">{t('look_through.unpriced')}</h3>
          <ul>
            {body.unpriced.map((u) => (
              <li key={u.ticker}>
                {u.name} ({u.ticker}) · {u.quantity}주
              </li>
            ))}
          </ul>
        </div>
      )}

      {(unpricedKinds.length > 0 || loose.length > 0) && (
        <div className="warning-list">
          <ul>
            {[...unpricedKinds, ...loose].map((w, i) => (
              <li key={`${w.kind}-${i}`}>{w.message}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}
