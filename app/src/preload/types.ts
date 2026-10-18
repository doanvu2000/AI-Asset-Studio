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
  attached?: boolean
}

export interface CoreConnection {
  baseUrl: string
  token: string
}

export interface McpInfo {
  command: string
  args: string[]
  coreDir: string
  packaged: boolean
}

export interface StudioBridge {
  getConnection(): Promise<CoreConnection>
  mcpInfo(): Promise<McpInfo>
  copyText(text: string): Promise<void>
  onCoreStatus(cb: (s: CoreStatus) => void): () => void
  platform: string
}
