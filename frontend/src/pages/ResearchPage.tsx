import { useEffect, useRef, useState } from 'react'
import type { FormEvent, KeyboardEvent } from 'react'
import { api } from '../api'
import ToolCallChip from '../components/ToolCallChip'
import type { ToolCall } from '../types'

interface Message {
  id: number
  role: 'user' | 'assistant'
  content: string
  toolCalls?: ToolCall[]
  isError?: boolean
}

const SUGGESTIONS = [
  '삼성전자 오늘 왜 올랐어? 최근 주가랑 관련 뉴스 같이 확인해줘.',
  '삼성전자 최근 3일 등락률이랑 거래량 알려줘.',
  'SK하이닉스 최근 공시 알려줘.',
  '주식 투자를 처음 시작할 때 알아야 할 기본 용어를 알려줘.',
]

export default function ResearchPage() {
  const [messages, setMessages] = useState<Message[]>([])
  const [input, setInput] = useState('')
  const [pending, setPending] = useState(false)
  const nextId = useRef(1)
  const endRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [messages, pending])

  async function send(text: string) {
    const question = text.trim()
    if (!question || pending) return

    setInput('')
    setMessages((prev) => [...prev, { id: nextId.current++, role: 'user', content: question }])
    setPending(true)

    try {
      const res = await api.askResearch(question)
      setMessages((prev) => [
        ...prev,
        { id: nextId.current++, role: 'assistant', content: res.answer, toolCalls: res.tool_calls },
      ])
    } catch (error) {
      const content = error instanceof Error ? error.message : '답변을 가져오지 못했어요.'
      setMessages((prev) => [
        ...prev,
        { id: nextId.current++, role: 'assistant', content, isError: true },
      ])
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

  return (
    <div className="chat-page">
      <div className="chat-scroll">
        <div className="container chat-column">
          {messages.length === 0 && !pending && (
            <div className="chat-empty">
              <h1>무엇이 궁금하세요?</h1>
              <p>종목의 주가, 뉴스, 공시를 근거로 답해드려요.</p>
              <div className="suggestions">
                {SUGGESTIONS.map((text) => (
                  <button key={text} type="button" className="suggestion" onClick={() => void send(text)}>
                    {text}
                  </button>
                ))}
              </div>
            </div>
          )}

          {messages.map((message) => (
            <div key={message.id} className={`message message-${message.role}`}>
              <div className={message.isError ? 'bubble bubble-error' : 'bubble'}>{message.content}</div>
              {message.toolCalls && message.toolCalls.length > 0 && (
                <div className="tool-calls">
                  <div className="tool-calls-title">사용한 도구</div>
                  {message.toolCalls.map((call, index) => (
                    <ToolCallChip key={index} call={call} />
                  ))}
                </div>
              )}
            </div>
          ))}

          {pending && (
            <div className="message message-assistant">
              <div className="bubble bubble-pending">
                <span className="dots" aria-hidden="true">
                  <i />
                  <i />
                  <i />
                </span>
                도구를 호출하고 답변을 만드는 중이에요
              </div>
            </div>
          )}
          <div ref={endRef} />
        </div>
      </div>

      <form className="composer" onSubmit={handleSubmit}>
        <div className="container composer-inner">
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="예: 삼성전자 최근 3일 등락률 알려줘"
            rows={1}
            aria-label="질문 입력"
          />
          <button type="submit" className="send" disabled={pending || !input.trim()}>
            보내기
          </button>
        </div>
      </form>
    </div>
  )
}
