import { resolve } from 'node:path'
import { defineConfig } from 'electron-vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  main: {},
  preload: {},
  renderer: {
    root: 'src/renderer',
    resolve: { alias: { '@': resolve('src/renderer/src') } },
    plugins: [vue()],
    // Core whitelists this exact origin (config.allowed_origins), so the port must not drift.
    server: { port: 5173, strictPort: true, host: 'localhost' },
    build: { target: 'chrome140' }
  }
})
