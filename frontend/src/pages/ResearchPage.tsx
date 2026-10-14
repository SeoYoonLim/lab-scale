import { useEffect, useRef, useState } from 'react'
import type { FormEvent, KeyboardEvent } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { api } from '../api'
import { IconSearch, IconSend, IconSparkle } from '../components/icons'
import DataToolCards from '../components/DataToolCards'
import ResearchProgress from '../components/ResearchProgress'
import SourceFootnotes from '../components/SourceFootnotes'
import TrendingWidget from '../components/TrendingWidget'
import type { Source } from '../types'
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
  disclaimer?: string
  errorMessage?: string
}

const SUGGESTIONS = [
  '삼성전자 오늘 왜 올랐어? 최근 주가랑 관련 뉴스 같이 확인해줘.',
  '삼성전자 최근 3일 등락률이랑 거래량 알려줘.',
  'SK하이닉스 최근 공시 알려줘.',
  '주식 투자를 처음 시작할 때 알아야 할 기본 용어를 알려줘.',
]

export default function ResearchPage() {
  const [entries, setEntries] = useState<Entry[]>([])
  const [input, setInput] = useState('')
  const [pending, setPending] = useState(false)
  // 직전 성공한 report_id. 있으면 다음 질문에 이어서(previous_report_id) 물어본다.
  // (backend/API.md: "그럼 최근 뉴스는?"처럼 종목명이 빠진 후속 질문도 이어받아 답함)
  const [threadReportId, setThreadReportId] = useState<number | null>(null)
  const nextId = useRef(1)
  const endRef = useRef<HTMLDivElement>(null)
  const location = useLocation()
  const navigate = useNavigate()

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [entries, pending])

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

  async function send(text: string) {
    const question = text.trim()
    if (!question || pending) return

    setInput('')
    const id = nextId.current++
    const previousReportId = threadReportId
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
                disclaimer: res.disclaimer,
              }
            : e,
        ),
      )
      // 저장에 실패하면(report_id: null) 이어받을 리포트가 없으니 대화 스레드를 끊는다.
      setThreadReportId(res.report_id)
    } catch (error) {
      const message = error instanceof Error ? error.message : '답변을 가져오지 못했어요.'
      setEntries((prev) => prev.map((e) => (e.id === id ? { ...e, status: 'error', errorMessage: message } : e)))
    } finally {
      setPending(false)
    }
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
            <span>#{threadReportId}에 이어서 대화 중이에요</span>
            <button type="button" onClick={() => setThreadReportId(null)}>
              새 질문으로 시작
            </button>
          </div>
        )}
        <div className="composer-row">
          <span className="composer-icon">
            <IconSearch />
          </span>
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="예: 삼성전자 최근 3일 등락률 알려줘"
            rows={1}
            aria-label="질문 입력"
          />
          <button type="submit" className="send send-icon" disabled={pending || !input.trim()} aria-label="질문 보내기">
            <IconSend />
          </button>
        </div>
      </div>
    </form>
  )

  return (
    <div className="chat-page">
      <div className="chat-scroll">
        <div className="container container-wide">
          {entries.length === 0 && (
            <div className="entry-empty">
              <h1>
                안녕하세요!
                <br />
                무엇을 분석해드릴까요?
              </h1>
              <p>종목의 주가, 뉴스, 공시를 근거로 답해드려요.</p>
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
              <DataToolCards />
              <TrendingWidget onPick={(question) => setInput(question)} />
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
                    <span className="entry-followup">#{entry.previousReportId}에 이어서</span>
                  )}
                  <span className="num">{formatTime(entry.askedAt)}</span>
                </div>
                <h2 className="entry-question">{entry.question}</h2>

                {entry.status === 'pending' && <ResearchProgress />}
                {entry.status === 'error' && <p className="entry-answer entry-error">{entry.errorMessage}</p>}
                {entry.status === 'done' && (
                  <div className="answer-card">
                    <div className="answer-head">
                      <span className="ai-badge">AI</span>
                      <span className="answer-title">분석 결과</span>
                    </div>
                    <p className="entry-answer">{entry.answer}</p>
                    <SourceFootnotes usedTools={entry.usedTools ?? []} sources={entry.sources ?? []} />
                    {entry.disclaimer && <p className="disclaimer-note muted small">{entry.disclaimer}</p>}
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
