import { defineConfig } from 'vite';
import { fileURLToPath } from 'node:url';

// Both HTML entry points must be compiled. A default Vite build only includes index.html.
export default defineConfig({
  build: {
    rollupOptions: {
      input: {
        index: fileURLToPath(new URL('./index.html', import.meta.url)),
        'api-reference': fileURLToPath(new URL('./api-reference.html', import.meta.url)),
      },
    },
  },
});
