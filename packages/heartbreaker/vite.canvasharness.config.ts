// Throwaway dev server for the voice canvas harness. Delete with canvasharness/.
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { resolve } from 'path'

export default defineConfig({
  root: resolve(__dirname, 'canvasharness'),
  plugins: [react()],
  resolve: { alias: { '@renderer': resolve(__dirname, 'src/renderer/src') } },
  server: { port: 5374 },
})
