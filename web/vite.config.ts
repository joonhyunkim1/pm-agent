import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// 개발 중에는 `pm serve`(8765)의 API로 프록시한다
export default defineConfig({
  plugins: [react()],
  server: { proxy: { '/api': 'http://127.0.0.1:8765' } },
})
