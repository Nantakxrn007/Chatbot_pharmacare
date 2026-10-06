import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

const apiTarget = process.env.VITE_API_TARGET || 'http://localhost:8899';

export default defineConfig({
  plugins: [react()],
  build: {
    // pdf.js worker (~1.4 MB) ใหญ่โดยธรรมชาติและโหลดเฉพาะตอนเปิด PDF — ไม่ใช่ปัญหา
    chunkSizeWarningLimit: 1500,
  },
  server: {
    port: 5173,
    proxy: {
      '/api': { target: apiTarget, changeOrigin: true },
      '/data': { target: apiTarget, changeOrigin: true },
    },
  },
});
