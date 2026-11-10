import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

// 별도 설정 파일로 둔다: vite.config.ts(dev 서버/빌드용 /api 프록시)와 섞으면 테스트 실행에는
// 필요 없는 설정이 같이 로드된다.
export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    globals: false,
  },
})
