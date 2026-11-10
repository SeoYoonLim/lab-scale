import { describe, expect, it } from 'vitest'
import appCss from './App.css?raw'
import indexCss from './index.css?raw'

// 파일 끝에 블록을 이어 붙이다가(브랜치 병합 포함) 규칙의 닫는 중괄호가 빠지면, 그 뒤의 모든 스타일이 앞 규칙 안에 중첩돼
// 조용히 적용되지 않는다(2026-11-11: .form-actions의 `}`가 빠져 시장 카드 막대 스타일이 전부 사라졌다). 화면을 열어 보지 않아도
// 잡히도록 중괄호 균형을 확인한다.
function braceDepth(css: string): { depth: number; negativeAtLine: number | null } {
  const stripped = css.replace(/\/\*[\s\S]*?\*\//g, (m) => m.replace(/[^\n]/g, ' ')).replace(/"[^"\n]*"|'[^'\n]*'/g, '""')
  let depth = 0
  let negativeAtLine: number | null = null
  let line = 1
  for (const ch of stripped) {
    if (ch === '\n') line += 1
    else if (ch === '{') depth += 1
    else if (ch === '}') {
      depth -= 1
      if (depth < 0 && negativeAtLine == null) negativeAtLine = line
    }
  }
  return { depth, negativeAtLine }
}

describe('전역 스타일시트', () => {
  it.each([
    ['App.css', appCss],
    ['index.css', indexCss],
  ])('%s: 여는 중괄호와 닫는 중괄호의 수가 같다', (_name, css) => {
    expect(braceDepth(css)).toEqual({ depth: 0, negativeAtLine: null })
  })

  it('braceDepth는 빠진 중괄호와 남는 중괄호를 잡아낸다', () => {
    expect(braceDepth('.a { color: red;\n.b { color: blue; }').depth).toBe(1)
    expect(braceDepth('.a { color: red; }\n}\n').negativeAtLine).toBe(2)
    // 주석·문자열 안의 중괄호는 세지 않는다
    expect(braceDepth('/* { */ .a { content: "}"; }').depth).toBe(0)
  })
})
