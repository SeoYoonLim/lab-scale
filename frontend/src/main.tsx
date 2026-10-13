import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { AuthProvider } from './auth/AuthContext.tsx'
import './index.css'
import App from './App.tsx'

// 기본 재시도를 끈다: API 에러는 이미 ApiError.status로 의미가 분명하고(401/404/409 등), react-query가
// 뒤에서 조용히 3번 재시도하면 화면의 "실패했어요" 표시가 실제보다 늦게 뜬다. 재시도가 필요한 폴링
// (CompanyPage의 실시간 시세)은 그 쿼리에서만 따로 켠다.
const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: false,
    },
  },
})

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <App />
      </AuthProvider>
    </QueryClientProvider>
  </StrictMode>,
)
