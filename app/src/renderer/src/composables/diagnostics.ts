import { ref } from 'vue'
import type { DiagPayload } from '../../../preload/types'
import { useConnection } from '@/stores/connection'
import { useToasts } from '@/stores/toasts'

const CORE_TIMEOUT_MS = 5000

/** Core snapshot when reachable; a stuck or dead core must not block grabbing the app-side logs. */
export async function collectPayload(): Promise<DiagPayload> {
  const t = useConnection().transport
  if (!t) return { diagnostics: null, error: 'core chưa kết nối' }
  let timer = 0
  try {
    const timeout = new Promise<never>((_, reject) => {
      timer = window.setTimeout(() => reject(new Error(`core không trả lời sau ${CORE_TIMEOUT_MS / 1000} giây`)), CORE_TIMEOUT_MS)
    })
    // Proxies (Pinia/Vue) cannot cross the IPC structured clone; plain JSON can.
    const d = await Promise.race([t.api.diagnostics(), timeout])
    return { diagnostics: JSON.parse(JSON.stringify(d)) as DiagPayload['diagnostics'] }
  } catch (e) {
    return { diagnostics: null, error: (e as Error).message }
  } finally {
    window.clearTimeout(timer)
  }
}

export function useDiagnosticsActions() {
  const toasts = useToasts()
  const busy = ref<'copy' | 'export' | null>(null)
  const available = !!window.studio

  async function copyLogs(): Promise<boolean> {
    const bridge = window.studio
    if (!bridge || busy.value) return false
    busy.value = 'copy'
    try {
      const r = await bridge.copyDiagnostics(await collectPayload())
      toasts.push(`Đã copy nhật ký (${Math.ceil(r.chars / 1024)} KB)`, 'success', 2600)
      return true
    } catch (e) {
      toasts.error(e, 'Không copy được nhật ký')
      return false
    } finally {
      busy.value = null
    }
  }

  async function exportLogs(): Promise<void> {
    const bridge = window.studio
    if (!bridge || busy.value) return
    busy.value = 'export'
    try {
      const r = await bridge.exportDiagnostics(await collectPayload())
      if (r) toasts.push(`Đã xuất ${r.files} file vào ${r.path}`, 'success', 4000)
    } catch (e) {
      toasts.error(e, 'Không xuất được nhật ký')
    } finally {
      busy.value = null
    }
  }

  async function openLogsDir(): Promise<void> {
    try {
      await window.studio?.openLogsDir()
    } catch (e) {
      toasts.error(e, 'Không mở được thư mục log')
    }
  }

  return { busy, available, copyLogs, exportLogs, openLogsDir }
}
