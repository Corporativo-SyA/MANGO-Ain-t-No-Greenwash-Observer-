import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// En desarrollo (npm run dev, puerto 5173) las llamadas a /api se reenvían al backend local.
// En producción el backend sirve frontend/dist en el mismo origen, así que no hace falta CORS.
const BACKEND = process.env.MANGO_BACKEND || 'http://127.0.0.1:8000';

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { '/api': { target: BACKEND, changeOrigin: true } },
  },
  build: { outDir: 'dist', emptyOutDir: true, chunkSizeWarningLimit: 900 },
});
