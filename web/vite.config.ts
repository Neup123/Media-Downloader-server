import { defineConfig } from 'vite';

export default defineConfig({
  base: '/',
  build: { outDir: 'dist', emptyOutDir: true, rollupOptions: { input: ['index.html', 'api-reference.html'] } },
  server: { host: '0.0.0.0', port: 5173 },
});
