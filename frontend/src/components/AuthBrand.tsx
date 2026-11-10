import { useLocale } from '../i18n/useLocale'

// 로그인/회원가입 카드 맨 위의 서비스 소개. 사이드바의 브랜드 영역과 같은 모양이다.
export default function AuthBrand() {
  const { t } = useLocale()
  return (
    <div className="auth-brand">
      <span className="brand-mark">AI</span>
      <span className="auth-brand-text">
        <strong>{t('layout.brand_name', { fallback: '투자 리서치 에이전트' })}</strong>
        <span className="muted small">{t('layout.brand_tagline', { fallback: 'AI가 찾아주는 스마트한 투자 인사이트' })}</span>
      </span>
    </div>
  )
}
