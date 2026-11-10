import { freshnessTone, orderedSectionKeys } from '../../api/contracts/briefing'
import type {
  BriefingContent,
  BriefingDisclosure,
  BriefingNewsItem,
  BriefingSectionKey,
  ChecklistSection,
  HoldingBriefingItem,
  HoldingsSection,
  MarketSection,
  RiskSection,
  WatchlistSection,
} from '../../api/contracts/briefing'
import type { SectionStatus } from '../../api/contracts/common'
import { useLocale } from '../../i18n/useLocale'
import { formatPlainPct, formatSignedPct, formatWon, kstDateOf, trendClass } from '../../utils/format'
import AssetTypeBadge from '../AssetTypeBadge'
import Banner from '../ui/Banner'
import StatusBadge from '../ui/StatusBadge'

// 뉴스·공시 시각은 오프셋이 붙은 ISO다(news·disclosure·market_news는 +09:00, indirect_news는 +00:00). 문자열 앞 10자를
// 날짜로 쓰지 않고 Date로 읽어 한국 날짜로 보여준다. 공시는 날짜만 아는 값(T00:00:00+09:00)이라 시각은 보여주지 않는다.
function NewsList({ items, label, emptyText }: { items: BriefingNewsItem[]; label?: string | null; emptyText?: string | null }) {
  const { t } = useLocale()
  if (items.length === 0) return emptyText ? <p className="muted small">{emptyText}</p> : null
  return (
    <div className="briefing-news">
      {label && <div className="muted small">{label}</div>}
      <ul>
        {items.map((n, i) => (
          <li key={`${n.title}-${i}`}>
            {n.url ? (
              <a href={n.url} target="_blank" rel="noreferrer">
                {n.title}
              </a>
            ) : (
              n.title
            )}{' '}
            <span className="muted small">
              {n.source} {kstDateOf(n.published_at)}
            </span>
            {n.similar_count >= 2 && <StatusBadge>{t('daily_briefing.similar_grouped', { vars: { n: n.similar_count } })}</StatusBadge>}
          </li>
        ))}
      </ul>
    </div>
  )
}

function DisclosureList({ items, emptyText }: { items: BriefingDisclosure[]; emptyText?: string | null }) {
  if (items.length === 0) return emptyText ? <p className="muted small">{emptyText}</p> : null
  return (
    <ul className="briefing-disclosures">
      {items.map((d, i) => (
        <li key={`${d.title}-${i}`}>
          {d.source_url ? (
            <a href={d.source_url} target="_blank" rel="noreferrer">
              {d.title}
            </a>
          ) : (
            d.title
          )}{' '}
          <span className="muted small">{kstDateOf(d.disclosed_at)}</span>
        </li>
      ))}
    </ul>
  )
}

// 영역 상태가 empty가 아니어도 partial·no_data면 제목 옆에 배지를 단다(핸드오프 5절).
function SectionHeader({ title, status }: { title: string; status: SectionStatus }) {
  const { t } = useLocale()
  return (
    <h3 className="briefing-section-title">
      {title}
      {(status === 'partial' || status === 'no_data') && <StatusBadge tone="warn">{t(`daily_briefing.section_status.${status}`)}</StatusBadge>}
    </h3>
  )
}

function MarketCard({ section }: { section: MarketSection }) {
  const { t } = useLocale()
  return (
    <section className="briefing-section">
      <SectionHeader title={section.title} status={section.status} />
      <div className="stat-grid">
        {section.indices.map((index) => (
          <div className="stat-card" key={index.code}>
            <span className="muted small">
              {index.name} {index.lag_business_days != null && index.lag_business_days > 0 && <StatusBadge tone="warn">{t('common.delayed_badge', { fallback: '지연' })}</StatusBadge>}
            </span>
            {/* 데이터가 없으면 date가 null이고 text에 "데이터 없음"이 온다 - 숫자 칸은 "—" */}
            <span className="stat-value num">{index.close != null ? index.close.toLocaleString('ko-KR') : '—'}</span>
            <span className={`num ${trendClass(index.change_pct)}`}>
              {formatSignedPct(index.change_pct)}
              <span className="muted small"> · {t('briefing.week_change', { fallback: '5일' })} {formatSignedPct(index.week_change_pct)}</span>
            </span>
            {index.date == null && <span className="muted small">{index.text}</span>}
          </div>
        ))}
      </div>
      <NewsList items={section.news} label={section.news_label} emptyText={section.news_empty_text} />
    </section>
  )
}

function HoldingCard({ item }: { item: HoldingBriefingItem }) {
  const { t } = useLocale()
  return (
    <article className="briefing-item">
      <header className="briefing-item-head">
        <strong>{item.name}</strong> <AssetTypeBadge assetType={item.asset_type ?? 'stock'} />
        <span className="muted small num">{item.ticker}</span>
        {item.lag_business_days != null && item.lag_business_days > 0 && <StatusBadge tone="warn">{t('common.delayed_badge', { fallback: '지연' })}</StatusBadge>}
      </header>
      {item.price_text && <p className="small num">{item.price_text}</p>}
      {item.position_text && <p className="muted small num">{item.position_text}</p>}
      <div className="meta small">
        <span className={`num ${trendClass(item.day_change_pct)}`}>{formatSignedPct(item.day_change_pct)}</span>
        <span className="muted">
          {t('briefing.eval_weight', {
            fallback: '평가 {value} · 비중 {weight}',
            vars: { value: formatWon(item.eval_amount), weight: formatPlainPct(item.weight_pct) },
          })}
        </span>
      </div>
      {item.risk_factors.length > 0 && (
        <ul className="briefing-risk-list">
          {item.risk_factors.map((r, i) => (
            <li key={i}>{r}</li>
          ))}
        </ul>
      )}
      <NewsList items={item.news} label={item.news_label} emptyText={item.news_empty_text} />
      {/* 직접 관련 뉴스와 간접(업종·산업) 뉴스는 서로 다른 상자로 보여준다(핸드오프 4-2절) */}
      {item.indirect_news && (
        <div className="briefing-indirect">
          <NewsList items={item.indirect_news.items} label={`${item.indirect_news.label}${item.indirect_news.topic ? ` · ${item.indirect_news.topic}` : ''}`} />
        </div>
      )}
      <DisclosureList items={item.disclosures} emptyText={item.disclosure_empty_text} />
    </article>
  )
}

function HoldingsCard({ section }: { section: HoldingsSection }) {
  return (
    <section className="briefing-section">
      <SectionHeader title={section.title} status={section.status} />
      {section.status === 'empty' ? <p className="muted">{section.message}</p> : section.items.map((item) => <HoldingCard key={item.ticker} item={item} />)}
    </section>
  )
}

function WatchlistCard({ section }: { section: WatchlistSection }) {
  const { t } = useLocale()
  const notable = section.items.filter((i) => i.notable)
  const quiet = section.items.filter((i) => !i.notable)
  return (
    <section className="briefing-section">
      <SectionHeader title={section.title} status={section.status} />
      {section.status === 'empty' && <p className="muted">{section.message}</p>}
      {notable.map((item) => (
        <article className="briefing-item" key={item.ticker}>
          <header className="briefing-item-head">
            <strong>{item.name}</strong> <span className="muted small num">{item.ticker}</span>
            <StatusBadge tone="accent">{t('daily_briefing.notable')}</StatusBadge>
          </header>
          {item.price_text && <p className="small num">{item.price_text}</p>}
          <ul className="briefing-risk-list">
            {item.changes.map((c, i) => (
              <li key={i}>{c}</li>
            ))}
          </ul>
          {item.since_research?.text && <p className="muted small">{item.since_research.text}</p>}
          <NewsList items={item.news} />
          <DisclosureList items={item.disclosures} />
        </article>
      ))}
      {quiet.length > 0 && (
        <ul className="briefing-quiet">
          {quiet.map((item) => (
            <li key={item.ticker}>
              {item.name} <span className="muted small">{item.status_text}</span>
            </li>
          ))}
        </ul>
      )}
      {section.dedup_text && <p className="muted small">{section.dedup_text}</p>}
    </section>
  )
}

function RiskCard({ section }: { section: RiskSection }) {
  const { t } = useLocale()
  const etf = section.etf_exposure
  return (
    <section className="briefing-section">
      <SectionHeader title={section.title} status={section.status} />
      {section.status === 'empty' ? (
        <p className="muted">{section.message ?? section.texts[0]}</p>
      ) : (
        <>
          <ul className="plain-list">
            {section.texts.map((text, i) => (
              <li key={i}>{text}</li>
            ))}
          </ul>
          {section.flags.length > 0 && (
            <ul className="flag-list-inline">
              {section.flags.map((f, i) => (
                <li key={`${f.code}-${i}`}>
                  <StatusBadge tone="warn">{f.code ? t(`diagnosis_flag.${f.code}`, { fallback: f.code }) : (f.kind ?? '')}</StatusBadge> {f.text}
                </li>
              ))}
            </ul>
          )}
          {section.sector && <p className="muted small">{section.sector.text}</p>}
          {/* ETF 실질 노출은 status가 computed일 때만 기업별 수치를 보여준다. not_computed면 이유(text)만(핸드오프 4-4절) */}
          {etf && etf.status === 'computed' && (
            <div className="briefing-etf">
              <p className="small">{etf.text}</p>
              <ul className="plain-list">
                {etf.top_companies.map((c) => (
                  <li key={`${c.code}-${c.name}`}>
                    {c.name} <span className="num">{formatPlainPct(c.weight_pct)}</span>
                  </li>
                ))}
              </ul>
              {etf.flags.map((f, i) => (
                <p key={i} className="muted small">
                  {f.text}
                </p>
              ))}
            </div>
          )}
          {etf && etf.status === 'not_computed' && <p className="muted small">{etf.text}</p>}
          {section.cautions.length > 0 && (
            <ul className="briefing-cautions">
              {section.cautions.map((c, i) => (
                <li key={i}>{c}</li>
              ))}
            </ul>
          )}
        </>
      )}
    </section>
  )
}

function ChecklistCard({ section }: { section: ChecklistSection }) {
  return (
    <section className="briefing-section briefing-checklist">
      <SectionHeader title={section.title} status={section.status} />
      {section.status === 'empty' ? (
        <p className="muted">{section.message}</p>
      ) : (
        <ol>
          {section.items.map((item, i) => (
            <li key={`${item.kind}-${i}`}>
              {item.url ? (
                <a href={item.url} target="_blank" rel="noreferrer">
                  {item.text}
                </a>
              ) : (
                item.text
              )}
            </li>
          ))}
        </ol>
      )}
    </section>
  )
}

// 브리핑 한 건의 본문. 영역 순서는 JSON 키 순서가 아니라 content.section_order를 따르되, 행동에 가장 가까운 "확인할 사항"은
// 요약 바로 아래에 올린다(핸드오프 9절 권장 배치).
export default function BriefingView({ content }: { content: BriefingContent }) {
  const { t } = useLocale()
  const tone = freshnessTone(content.freshness.status)
  const order = orderedSectionKeys(content).filter((k) => k !== 'checklist')
  const render: Record<BriefingSectionKey, () => React.ReactNode> = {
    market: () => content.sections.market && <MarketCard key="market" section={content.sections.market} />,
    holdings: () => content.sections.holdings && <HoldingsCard key="holdings" section={content.sections.holdings} />,
    watchlist: () => content.sections.watchlist && <WatchlistCard key="watchlist" section={content.sections.watchlist} />,
    risk: () => content.sections.risk && <RiskCard key="risk" section={content.sections.risk} />,
    checklist: () => null,
  }

  return (
    <div className="briefing-view">
      {/* 데이터가 최신이 아니면 헤드라인을 경고 띠로 맨 위에(delayed는 노랑, stale·no_data는 빨강) */}
      {content.freshness.warning && (
        <Banner tone={tone === 'none' ? 'info' : tone} title={t(`daily_briefing.market_data_status.${content.market_data_status}`)}>
          <p>{content.freshness.headline}</p>
          {content.freshness.notices.map((n, i) => (
            <p key={i} className="muted small">
              {n}
            </p>
          ))}
        </Banner>
      )}
      {!content.freshness.warning && <p className="muted small">{content.freshness.headline}</p>}

      <section className="briefing-section briefing-summary">
        <div className="briefing-summary-head">
          <StatusBadge tone={content.summary.source === 'llm' ? 'accent' : 'neutral'}>
            {content.summary.source === 'llm' ? t('daily_briefing.summary_llm') : t('daily_briefing.summary_code')}
          </StatusBadge>
          {content.summary.note && <span className="muted small">{content.summary.note}</span>}
        </div>
        <p>{content.summary.text}</p>
      </section>

      {content.sections.checklist && <ChecklistCard section={content.sections.checklist} />}
      {order.map((key) => render[key]())}

      <p className="muted small">{content.not_advice}</p>
      <p className="disclaimer-note muted small">{content.disclaimer}</p>
    </div>
  )
}
