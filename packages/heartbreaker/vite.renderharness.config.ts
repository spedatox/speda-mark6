import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { resolve } from 'path'
import { tmpdir } from 'os'

// Isolated UI fixtures using the production message renderer, without an API key.
export default defineConfig({
  root: resolve(__dirname, 'renderharness'),
  publicDir: resolve(__dirname, 'src/renderer/public'),
  cacheDir: resolve(tmpdir(), 'speda-inline-rendering-vite'),
  plugins: [react()],
  html: { cspNonce: 'rendering-qa' },
  server: { port: 5375, strictPort: true },
})
