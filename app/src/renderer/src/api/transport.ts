import { createHttpApi, type StudioApi } from './client'
import { StudioSocket, type EventStream } from './ws'

export interface Transport {
  api: StudioApi
  stream: EventStream
  mock: boolean
}

export const MOCK = import.meta.env.VITE_MOCK === '1'

export async function createMockTransport(): Promise<Transport> {
  const { MockCore } = await import('./mock/server')
  const core = new MockCore()
  return { api: core, stream: core, mock: true }
}

export function createRealTransport(baseUrl: string, token: string): Transport {
  return { api: createHttpApi(baseUrl, token), stream: new StudioSocket(baseUrl, token), mock: false }
}
