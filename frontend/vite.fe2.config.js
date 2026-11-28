import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

const backendPort = Number(process.env.FE2_BACKEND_PORT || 8766);

export default defineConfig({
  plugins: [react()],
  server: {
    host: '127.0.0.1',
    port: 5174,
    strictPort: true,
    proxy: { '/api': `http://127.0.0.1:${backendPort}` },
  },
  preview: { port: 4174 },
});
