import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// In development the API and WebSocket are proxied to the Python backend.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': 'http://127.0.0.1:8000',
      '/ws': { target: 'ws://127.0.0.1:8000', ws: true },
    },
  },
  build: { chunkSizeWarningLimit: 1600 },
})
