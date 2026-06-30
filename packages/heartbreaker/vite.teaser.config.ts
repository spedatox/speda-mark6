import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { resolve } from 'path'

/**
 * Cinematic teaser entry — a SECOND Vite app that lives inside heartbreaker and
 * imports the REAL renderer source (`@renderer/*`). It is intentionally
 * project-dependent: the teaser plays the actual components and theme engine, so
 * it can never visually drift from the product. Run side-by-side with the app
 * (desktop 5173 / web 5273 / teaser 5373).
 */
export default defineConfig({
  root: resolve(__dirname, 'src/teaser'),
  plugins: [react()],
  resolve: {
    alias: {
      '@renderer': resolve(__dirname, 'src/renderer/src'),
    },
  },
  build: {
    outDir: resolve(__dirname, 'out/teaser'),
    emptyOutDir: true,
  },
  server: {
    port: 5373,
  },
})
