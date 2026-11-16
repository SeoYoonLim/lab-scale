import { Component, type ErrorInfo, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { useLocale } from '../../i18n/useLocale'

function Fallback() {
  const { t } = useLocale()
  return (
    <div className="container page error-boundary" role="alert">
      <h1 className="error-boundary-title">{t('errors.boundary_title', { fallback: '화면을 표시하지 못했어요' })}</h1>
      <p className="muted">
        {t('errors.boundary_body', { fallback: '일시적인 문제일 수 있어요. 새로고침해도 계속되면 잠시 후 다시 시도해주세요.' })}
      </p>
      <div className="error-boundary-actions">
        <button type="button" className="send" onClick={() => window.location.reload()}>
          {t('errors.reload', { fallback: '새로고침' })}
        </button>
        <Link className="btn" to="/">
          {t('common.go_home', { fallback: '처음 화면으로' })}
        </Link>
      </div>
    </div>
  )
}

// 화면을 그리다 예외가 나거나(렌더 오류), 배포 직후 옛 코드 조각을 못 받아(lazy 청크 로딩 실패) 흰 화면이 되는 것을 막는다.
// 경로가 바뀌면 resetKey가 달라져 자동으로 풀린다.
type State = { failed: boolean; resetKey?: string }

export default class ErrorBoundary extends Component<{ children: ReactNode; resetKey?: string }, State> {
  state: State = { failed: false, resetKey: this.props.resetKey }

  static getDerivedStateFromError() {
    return { failed: true }
  }

  static getDerivedStateFromProps(props: { resetKey?: string }, state: State): Partial<State> | null {
    return props.resetKey !== state.resetKey ? { failed: false, resetKey: props.resetKey } : null
  }

  componentDidCatch(error: unknown, info: ErrorInfo) {
    console.error('화면 렌더 오류', error, info.componentStack)
  }

  render() {
    return this.state.failed ? <Fallback /> : this.props.children
  }
}
