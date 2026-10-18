export type CoreState = 'stopped' | 'starting' | 'ready' | 'restarting' | 'error'

export interface CoreStatus {
  state: CoreState
  attempt: number
  baseUrl?: string
  pid?: number
  message?: string
  detail?: string
  retryInMs?: number
  mock?: boolean
}

export interface CoreConnection {
  baseUrl: string
  token: string
}

export interface StudioBridge {
  getConnection(): Promise<CoreConnection>
  onCoreStatus(cb: (s: CoreStatus) => void): () => void
  platform: string
}
