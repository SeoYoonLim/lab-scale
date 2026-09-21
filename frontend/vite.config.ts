import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    // 백엔드(FastAPI) 기본 포트. VITE_USE_MOCK=false 일 때 /api 요청을 여기로 넘긴다.
    proxy: {
      '/api': 'http://localhost:8000',
    },
  },
})
