import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    proxy: {
      // Dev API + websocket proxy. Server runs on API_PORT=8002 (.env).
      '/api': 'http://127.0.0.1:8002',
      '/ws': { target: 'ws://127.0.0.1:8002', ws: true },
    },
  },
})
