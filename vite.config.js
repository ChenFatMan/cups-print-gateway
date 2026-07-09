import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  root: 'frontend',
  base: '/',
  plugins: [react()],
  build: {
    outDir: '../src/print_gateway/web/dist',
    emptyOutDir: true
  },
  server: {
    proxy: {
      '/api': 'http://127.0.0.1:8000'
    }
  }
});
