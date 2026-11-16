import { useEffect, useRef } from 'react'
import { useLocation } from 'react-router-dom'
import { useLocale } from '../i18n/useLocale'

// 화면(경로)이 바뀔 때 키보드·화면 읽기 프로그램 사용자가 "새 화면이 열렸다"는 걸 알 수 있게 한다.
//  1) 탭 제목(document.title)을 화면 제목(h1)으로 맞춘다. 로그인 화면도 같다.
//  2) 큰 구역이 바뀌는 이동(/companies → /portfolio 등)이면 새 화면의 h1로 초점을 옮긴다.
//     같은 구역 안의 이동(포트폴리오 탭끼리 등)은 사용자가 누르던 링크에서 초점을 빼앗지 않으려고 옮기지 않는다.
// 화면은 lazy로 늦게 그려지고 데이터가 와야 제목이 정해지는 곳도 있어서, 화면이 바뀌어 있는 동안 DOM 변화를 지켜본다.
export default function RouteA11y() {
  const { pathname } = useLocation()
  const { t } = useLocale()
  const brand = t('layout.brand_name', { fallback: '투자 리서치 에이전트' })
  const researchLabel = t('nav.research')
  const prevSection = useRef<string | null>(null)
  // 직전 화면에서 마지막으로 본 h1. 화면 전환이 늦어지는 동안(lazy 로딩 등) 이전 화면의 h1이 아직 남아 있을 수 있어서,
  // 이 요소는 새 화면의 제목으로 치지 않는다. 새 화면이 곧바로 그려지면 h1이 다른 요소라서 바로 새 제목으로 인정된다.
  const lastHeading = useRef<Element | null>(null)

  useEffect(() => {
    const section = pathname.split('/')[1] ?? ''
    const sectionChanged = prevSection.current !== null && prevSection.current !== section
    prevSection.current = section

    const staleHeading = sectionChanged ? lastHeading.current : null
    let focusPending = sectionChanged

    const sync = () => {
      const found = document.querySelector('h1')
      if (found) lastHeading.current = found
      const h1 = found && found !== staleHeading ? found : null
      // 리서치 화면의 h1은 인사말("무엇을 분석해드릴까요?")이라 제목으로는 메뉴 이름을 쓴다.
      const heading = pathname === '/' ? researchLabel : h1?.textContent?.trim()
      if (heading) {
        const title = `${heading} · ${brand}`
        if (document.title !== title) document.title = title
      }
      if (focusPending && h1) {
        focusPending = false
        h1.setAttribute('tabindex', '-1')
        h1.focus({ preventScroll: true })
      }
    }

    sync()
    const observer = new MutationObserver(sync)
    observer.observe(document.getElementById('root') ?? document.body, { childList: true, subtree: true, characterData: true })
    return () => observer.disconnect()
  }, [pathname, brand, researchLabel])

  return null
}
