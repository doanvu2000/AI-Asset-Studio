/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_MOCK?: string
}

declare const __APP_VERSION__: string

interface ImportMeta {
  readonly env: ImportMetaEnv
}
