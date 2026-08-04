// Throwaway dev server for the MapBlock harness. Delete with mapharness/.
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { resolve } from 'path'

export default defineConfig({
  root: resolve(__dirname, 'mapharness'),
  plugins: [react()],
  resolve: { alias: { '@renderer': resolve(__dirname, 'src/renderer/src') } },
  server: { port: 5373 },
})
