import { useLocale } from '../../i18n/useLocale'

// target="_blank" 링크 안에 넣어서, 화면 읽기 프로그램 사용자에게 "새 탭에서 열린다"는 걸 알려준다(화면에는 보이지 않는다).
export default function NewTabHint() {
  const { t } = useLocale()
  return <span className="sr-only"> ({t('a11y.opens_new_tab', { fallback: '새 탭에서 열림' })})</span>
}
