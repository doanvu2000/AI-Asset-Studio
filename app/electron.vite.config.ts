import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { defineConfig } from 'electron-vite'
import vue from '@vitejs/plugin-vue'

const { version } = JSON.parse(readFileSync(resolve('package.json'), 'utf8')) as { version: string }

export default defineConfig({
  main: {},
  preload: {},
  renderer: {
    root: 'src/renderer',
    resolve: { alias: { '@': resolve('src/renderer/src') } },
    plugins: [vue()],
    define: { __APP_VERSION__: JSON.stringify(version) },
    // Core whitelists this exact origin (config.allowed_origins), so the port must not drift.
    server: { port: 5173, strictPort: true, host: 'localhost' },
    build: { target: 'chrome140' }
  }
})
