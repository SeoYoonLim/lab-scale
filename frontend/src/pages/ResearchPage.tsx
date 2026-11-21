import { useEffect, useRef, useState } from 'react'
import type { FormEvent, KeyboardEvent } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { api } from '../api'
import { useLocale } from '../i18n/useLocale'
import ResearchAnswerBody from '../components/ResearchAnswerBody'
import { IconSearch, IconSend, IconSparkle } from '../components/icons'
import DataToolCards from '../components/DataToolCards'
import MarketSummaryCard from '../components/MarketSummaryCard'
import ResearchProgress from '../components/ResearchProgress'
import SourceFootnotes from '../components/SourceFootnotes'
import SourceStatusNote from '../components/SourceStatusNote'
import AnalysisBasisBadge from '../components/AnalysisBasisBadge'
import ErrorNotice from '../components/ui/ErrorNotice'
import TrendingWidget from '../components/TrendingWidget'
import type { TranslationStatus } from '../api/contracts/common'
import type { ResolvedInstrument, Source, SourceStatus } from '../types'
import { stripDuplicateDisclaimer } from '../utils/disclaimer'
import { formatTime } from '../utils/format'

// 질문 하나 = 장부 항목 하나. reportId는 응답이 오기 전까지 없고(pending),
// 저장에 실패하면 계속 null로 남는다(그래도 답변 자체는 보여준다).
interface Entry {
  id: number
  question: string
  askedAt: string
  status: 'pending' | 'done' | 'error'
  reportId?: number | null
  previousReportId?: number | null
  answer?: string
  usedTools?: string[]
  sources?: Source[]
  sourceStatus?: SourceStatus | null
  disclaimer?: string
  // 실제로 답한 언어와 번역 상태(핸드오프 15-1절) - 번역문인지, 번역 검증에 실패해 요약만 온 건지 구분해 보여준다
  locale?: string
  translationStatus?: TranslationStatus | null
  // 분석 기준 종목과 되묻기 여부(2026-11-21). 예전 백엔드는 주지 않아 undefined다
  resolvedInstruments?: ResolvedInstrument[]
  clarificationRequired?: boolean
  error?: unknown
}

// 추천 질문은 실제로 백엔드에 보내는 문장이다. 질문 해석(라우팅·용어 사전·전망 판정)이 한국어 규칙이라 번역하지 않는다
// (docs/FRONTEND_HANDOFF_I18N_HISTORY.md: 질문 입력은 당분간 한국어 안내를 권장). 답변은 설정한 언어로 온다.
const SUGGESTIONS = [
  '삼성전자 오늘 왜 올랐어? 최근 주가랑 관련 뉴스 같이 확인해줘.',
  '삼성전자 최근 3일 등락률이랑 거래량 알려줘.',
  'SK하이닉스 최근 공시 알려줘.',
  '주식 투자를 처음 시작할 때 알아야 할 기본 용어를 알려줘.',
]

// 지원 범위: 백엔드 수집 대상(backend/scripts/build_universe.py 시가총액 상위 300·build_etf_universe.py ETF 50). 2026-11-18 DB company 테이블에서
// 주식 300·ETF 50을 확인했다. 대상이 바뀌면 이 값도 바꾼다.
const SUPPORTED_UNIVERSE = { stocks: 300, etfs: 50 }

export default function ResearchPage() {
  const { t, locale } = useLocale()
  const [entries, setEntries] = useState<Entry[]>([])
  const [input, setInput] = useState('')
  const [pending, setPending] = useState(false)
  // 화면 읽기 프로그램용 알림. 진행 카드가 답변 카드로 바뀌는 건 소리로는 알 수 없어서 따로 알려준다.
  const [announcement, setAnnouncement] = useState('')
  // 가장 최근에 성공한 답변의 report_id. 이 답변 아래에만 "이어서 묻기" 버튼이 보인다.
  const [lastReportId, setLastReportId] = useState<number | null>(null)
  // 이어서 묻기를 켠 report_id. 있으면 다음 질문 "한 번"만 이어서(previous_report_id) 물어본다.
  // 기본은 새 질문이다: 자동으로 이어지면 다른 종목을 물을 때마다 끊어야 하고, 종목을 못 찾는 질문이
  // 직전 종목의 답으로 둔갑하는 일도 생긴다. (backend/API.md: "그럼 최근 뉴스는?"처럼 종목명이 빠진 후속 질문용)
  const [threadReportId, setThreadReportId] = useState<number | null>(null)
  const nextId = useRef(1)
  const endRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)
  const location = useLocation()
  const navigate = useNavigate()

  useEffect(() => {
    // 부드러운 스크롤은 CSS의 prefers-reduced-motion을 따르지 않으므로 직접 확인한다
    const reduce = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
    endRef.current?.scrollIntoView({ behavior: reduce ? 'auto' : 'smooth', block: 'end' })
  }, [entries, pending])

  // 첫 질문을 보내면 입력창이 가운데(안내 화면)에서 아래쪽 고정 칸으로 옮겨 가며 새로 만들어져서, 키보드로 보낸 사용자의
  // 초점이 사라진다(추천 질문 버튼을 눌러 보낸 경우도 그 버튼이 사라진다). 새 입력창으로 초점을 옮긴다.
  const hasEntries = entries.length > 0
  const hadEntries = useRef(false)
  useEffect(() => {
    if (hasEntries && !hadEntries.current) inputRef.current?.focus({ preventScroll: true })
    hadEntries.current = hasEntries
  }, [hasEntries])

  // 종목 상세 화면의 "이 종목으로 리서치해줘" 버튼처럼, 다른 화면에서 질문을 채워 넣고 넘어온 경우.
  // 한 번 소비하면 라우터 state를 비워서 뒤로/앞으로가기로 다시 채워지지 않게 한다.
  useEffect(() => {
    const prefillQuestion = (location.state as { prefillQuestion?: string } | null)?.prefillQuestion
    if (prefillQuestion) {
      setInput(prefillQuestion)
      navigate('.', { replace: true, state: null })
    }
    // oxlint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // previous: 이 질문이 이어받을 답변. 다시 시도는 그 질문이 원래 이어받던 답변을 그대로 넘긴다.
  async function send(text: string, previous: number | null = threadReportId) {
    const question = text.trim()
    if (!question || pending) return

    setInput('')
    setAnnouncement('')
    // 이어서 묻기는 한 번만 쓴다
    setThreadReportId(null)
    const id = nextId.current++
    const previousReportId = previous
    setEntries((prev) => [
      ...prev,
      { id, question, askedAt: new Date().toISOString(), status: 'pending', previousReportId },
    ])
    setPending(true)

    try {
      const res = await api.askResearch(question, previousReportId)
      setEntries((prev) =>
        prev.map((e) =>
          e.id === id
            ? {
                ...e,
                status: 'done',
                reportId: res.report_id,
                previousReportId: res.previous_report_id,
                answer: res.answer,
                usedTools: res.used_tools,
                sources: res.sources,
                sourceStatus: res.source_status ?? null,
                disclaimer: res.disclaimer,
                locale: res.locale,
                translationStatus: res.translation_status ?? null,
                resolvedInstruments: res.resolved_instruments ?? [],
                clarificationRequired: res.clarification_required ?? false,
              }
            : e,
        ),
      )
      setAnnouncement(t('research.answer_ready', { fallback: '분석 결과가 도착했어요.' }))
      // 저장에 실패하면(report_id: null) 이어받을 리포트가 없으니 이어서 묻기 버튼이 나오지 않는다.
      setLastReportId(res.report_id)
    } catch (error) {
      setEntries((prev) => prev.map((e) => (e.id === id ? { ...e, status: 'error', error } : e)))
    } finally {
      setPending(false)
    }
  }

  // 오늘의 관심 종목을 누르면 질문만 채우고 보내지는 않는다 - 채워졌다는 걸 알리고 입력창으로 초점을 옮겨 바로 확인·전송하게 한다.
  function pickQuestion(question: string) {
    setInput(question)
    setAnnouncement(t('research.question_filled', { fallback: '질문이 입력창에 채워졌어요. 확인하고 보내주세요.' }))
    inputRef.current?.focus()
  }

  // 최근 답변 아래 버튼: 이어서 묻기를 켜고 끈다. 켜면 입력창으로 초점을 옮긴다.
  function toggleFollowUp(reportId: number) {
    if (threadReportId === reportId) {
      setThreadReportId(null)
      setAnnouncement(t('research.followup_off_status', { fallback: '이어서 묻기를 껐어요. 새 질문으로 보내요.' }))
      return
    }
    setThreadReportId(reportId)
    setAnnouncement(t('research.followup_on_status', { fallback: '#{id} 답변에 이어서 질문해요.', vars: { id: reportId } }))
    inputRef.current?.focus()
  }

  function handleSubmit(e: FormEvent) {
    e.preventDefault()
    void send(input)
  }

  function handleKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    // 한글 조합 중 Enter 는 전송으로 보지 않는다.
    if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault()
      void send(input)
    }
  }

  const composer = (
    <form className={entries.length === 0 ? 'composer composer-hero' : 'composer'} onSubmit={handleSubmit}>
      <div className="container container-wide composer-inner">
        {threadReportId != null && (
          <div className="thread-hint">
            <span>{t('research.continuing', { fallback: '#{id}에 이어서 대화 중이에요', vars: { id: threadReportId } })}</span>
            <button
              type="button"
              onClick={() => {
                // 이 버튼은 스레드가 끊기면 사라지므로 초점을 입력창으로 옮긴다
                setThreadReportId(null)
                inputRef.current?.focus()
              }}
            >
              {t('research.new_question', { fallback: '새 질문으로 시작' })}
            </button>
          </div>
        )}
        <div className="composer-row">
          <span className="composer-icon">
            <IconSearch />
          </span>
          <textarea
            ref={inputRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={t('research.input_placeholder', { fallback: '예: 삼성전자 최근 3일 등락률 알려줘' })}
            rows={1}
            aria-label={t('research.input_aria', { fallback: '질문 입력' })}
          />
          <button type="submit" className="send send-icon" disabled={pending || !input.trim()} aria-label={t('research.send_aria', { fallback: '질문 보내기' })}>
            <IconSend />
          </button>
        </div>
        {/* 지원 범위: 종목 목록에 없는 해외 종목을 물으면 "찾지 못했다"만 나오므로 미리 알려준다 */}
        <p className="muted small scope-hint">
          {t('research.scope_hint', {
            fallback: '현재 등록된 국내 주식 {stocks}종목과 ETF {etfs}개를 지원해요(예: 삼성전자). 그 외 종목과 해외 주식은 지원하지 않을 수 있어요.',
            vars: SUPPORTED_UNIVERSE,
          })}
        </p>
        {/* 한국어가 아닌 언어로 쓰는 사용자에게: 질문은 한국어가 가장 정확하다 */}
        {locale !== 'ko-KR' && (
          <p className="muted small question-hint">
            {t('research.question_hint', {
              fallback: '질문은 한국어가 가장 정확해요. 답변은 선택한 언어로 와요.',
            })}
          </p>
        )}
      </div>
    </form>
  )

  return (
    <div className="chat-page">
      <p className="sr-only" role="status">
        {announcement}
      </p>
      <div className="chat-scroll">
        {/* 첫 질문 뒤에는 안내 문구(h1)가 사라지므로, 화면 제목은 화면 읽기 프로그램용으로 남긴다 */}
        {entries.length > 0 && <h1 className="sr-only">{t('nav.research')}</h1>}
        <div className="container container-wide">
          {entries.length === 0 && (
            <div className="entry-empty">
              <h1>
                {t('research.hello', { fallback: '안녕하세요!' })}
                <br />
                {t('research.what_to_analyze', { fallback: '무엇을 분석해드릴까요?' })}
              </h1>
              <p>{t('research.basis_note', { fallback: '종목의 주가, 뉴스, 공시를 근거로 답해드려요.' })}</p>
              {composer}
              <ul className="suggestion-list">
                {SUGGESTIONS.map((text) => (
                  <li key={text}>
                    <button type="button" className="suggestion" onClick={() => void send(text)}>
                      <IconSparkle />
                      <span>{text}</span>
                    </button>
                  </li>
                ))}
              </ul>
              <MarketSummaryCard />
              <DataToolCards />
              <TrendingWidget onPick={pickQuestion} />
            </div>
          )}

          <div className="entries">
            {entries.map((entry) => (
              <article className="entry" key={entry.id}>
                <div className="entry-head">
                  <span className="entry-number num">
                    {entry.reportId != null ? `#${entry.reportId}` : entry.status === 'pending' ? '…' : '—'}
                  </span>
                  {entry.previousReportId != null && (
                    <span className="entry-followup">
                      {t('research.followup_of', { fallback: '#{id}에 이어서', vars: { id: entry.previousReportId } })}
                    </span>
                  )}
                  <span className="num">{formatTime(entry.askedAt)}</span>
                </div>
                <h2 className="entry-question">{entry.question}</h2>

                {entry.status === 'pending' && <ResearchProgress />}
                {entry.status === 'error' && (
                  <ErrorNotice
                    error={entry.error}
                    fallback={t('research.answer_failed', { fallback: '답변을 가져오지 못했어요.' })}
                    onRetry={() => void send(entry.question, entry.previousReportId ?? null)}
                  />
                )}
                {entry.status === 'done' && (
                  <div className="answer-card">
                    <div className="answer-head">
                      <span className="ai-badge">AI</span>
                      <span className="answer-title">{t('research.answer_title', { fallback: '분석 결과' })}</span>
                    </div>
                    {!entry.clarificationRequired && <AnalysisBasisBadge instruments={entry.resolvedInstruments ?? []} />}
                    <ResearchAnswerBody
                      answer={stripDuplicateDisclaimer(entry.answer ?? '', entry.disclaimer)}
                      locale={entry.locale}
                      translationStatus={entry.translationStatus}
                      reportId={entry.reportId ?? null}
                    />
                    <SourceFootnotes usedTools={entry.usedTools ?? []} sources={entry.sources ?? []} />
                    <SourceStatusNote sources={entry.sources ?? []} status={entry.sourceStatus} />
                    {entry.disclaimer && <p className="disclaimer-note muted small">{entry.disclaimer}</p>}
                  </div>
                )}
                {entry.status === 'done' && entry.reportId != null && entry.reportId === lastReportId && !pending && (
                  <div className="entry-actions">
                    <button
                      type="button"
                      className="btn followup-btn"
                      aria-pressed={threadReportId === entry.reportId}
                      onClick={() => toggleFollowUp(entry.reportId as number)}
                    >
                      {t('research.followup_btn', { fallback: '이 답변에 이어서 묻기' })}
                    </button>
                  </div>
                )}
              </article>
            ))}
          </div>
          <div ref={endRef} />
        </div>
      </div>

      {entries.length > 0 && composer}
    </div>
  )
}
