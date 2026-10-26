import { defineStore } from 'pinia'
import { computed, ref, shallowRef } from 'vue'
import type { CoreStatus } from '../../../preload/types'
import { createMockTransport, createRealTransport, MOCK, type Transport } from '@/api/transport'
import type { SocketState } from '@/api/ws'
import type { StudioApi } from '@/api/client'
import { applyEvents, resyncAll } from './events'
import { devlog } from '@/composables/devlog'

export const useConnection = defineStore('connection', () => {
  const core = ref<CoreStatus>({ state: 'starting', attempt: 0 })
  const socket = ref<SocketState>('connecting')
  const transport = shallowRef<Transport | null>(null)
  const lastSync = ref(0)
  let currentBase = ''

  const online = computed(() => core.value.state === 'ready' && socket.value === 'open')

  function attach(t: Transport): void {
    transport.value?.stream.stop()
    transport.value = t
    t.stream.start({
      batch: applyEvents,
      resync: () => {
        void resyncAll().then(() => (lastSync.value = Date.now()))
      },
      state: (s) => {
        if (s !== socket.value) devlog(s === 'open' || s === 'connecting' ? 'info' : 'warn', `socket ${socket.value} → ${s}`)
        socket.value = s
      }
    })
  }

  async function init(): Promise<void> {
    if (MOCK) {
      core.value = { state: 'ready', attempt: 0, mock: true, baseUrl: 'mock://core' }
      attach(await createMockTransport())
      return
    }
    const bridge = window.studio
    if (!bridge) {
      core.value = { state: 'error', attempt: 0, message: 'Không có cầu nối Electron. Mở bằng app hoặc chạy npm run dev:mock.' }
      return
    }
    bridge.onCoreStatus((s) => {
      core.value = s
      if (s.state === 'ready' && s.baseUrl && s.baseUrl !== currentBase) {
        currentBase = s.baseUrl
        void bridge.getConnection().then((c) => attach(createRealTransport(c.baseUrl, c.token)))
      }
    })
  }

  return { core, socket, transport, online, lastSync, init }
})

export function api(): StudioApi {
  const t = useConnection().transport
  if (!t) throw new Error('Core chưa sẵn sàng')
  return t.api
}

/** Applies WS events still waiting for the next frame, so a snapshot fetched after this is never older than them. */
export function flushEvents(): void {
  useConnection().transport?.stream.flush()
}

export function blobUrl(hash: string): string {
  return useConnection().transport?.api.blobUrl(hash) ?? ''
}

export function thumbUrl(hash: string, size = 256): string {
  return useConnection().transport?.api.thumbUrl(hash, size) ?? ''
}
