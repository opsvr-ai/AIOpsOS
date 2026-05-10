import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import path from 'path';

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  server: {
    port: 5173,
    watch: { usePolling: true },
    proxy: {
      '/uploads': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
      '/health': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
        ws: true,
        onProxyError: (err, _req, res) => {
          // ECONNREFUSED or other connection errors during backend cold-start window.
          // Rewrite to 503 + cold_start flag instead of the default 500,
          // so the frontend axios interceptor can distinguish cold-start from real errors.
          if (!res.headersSent) {
            res.writeHead(503, { 'Content-Type': 'application/json' });
          }
          res.end(JSON.stringify({ ready: false, cold_start: true }));
        },
      },
    },
  },
  test: {
    globals: true,
    environment: 'node',
    include: ['tests/**/*.spec.ts', 'src/**/*.spec.ts', 'src/**/*.test.ts'],
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
});
