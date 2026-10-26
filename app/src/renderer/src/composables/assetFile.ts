import { ref } from 'vue'
import { useToasts } from '@/stores/toasts'
import type { BundleEngine } from '../../../preload/types'

export type AssetBusy = 'save' | 'reveal' | 'copy' | 'bundle' | null

const baseName = (p: string): string => p.split(/[\\/]/).pop() ?? p
const prepared = new Set<string>()

/** File actions on a blob through the Electron bridge; every failure ends in a toast, never an unhandled rejection. */
export function useAssetFile() {
  const toasts = useToasts()
  const bridge = window.studio?.asset
  const busy = ref<AssetBusy>(null)
  const copied = ref(false)
  let copiedTimer = 0

  async function run<T>(kind: Exclude<AssetBusy, null>, fn: () => Promise<T>, failMsg: string): Promise<T | undefined> {
    if (!bridge || busy.value) return undefined
    busy.value = kind
    try {
      return await fn()
    } catch (e) {
      toasts.error(e, failMsg)
      return undefined
    } finally {
      busy.value = null
    }
  }

  async function saveAs(hash: string, name?: string): Promise<void> {
    const dest = await run('save', () => bridge!.saveAs(hash, name), 'Không xuất được')
    if (dest) toasts.push(`Đã xuất ${baseName(dest)}`, 'success', 3200)
  }

  async function reveal(hash: string, name?: string): Promise<void> {
    await run('reveal', () => bridge!.reveal(hash, name), 'Không mở được thư mục')
  }

  async function copyPath(hash: string, name?: string): Promise<void> {
    const path = await run('copy', () => bridge!.copyPath(hash, name), 'Không copy được đường dẫn')
    if (!path) return
    copied.value = true
    window.clearTimeout(copiedTimer)
    copiedTimer = window.setTimeout(() => (copied.value = false), 1600)
    toasts.push('Đã copy đường dẫn', 'success', 2000)
  }

  // Warm the export copy so the drag can start synchronously when the pointer actually moves.
  function prepare(hash: string, name?: string): void {
    const key = `${hash}\n${name ?? ''}`
    if (!bridge || prepared.has(key)) return
    prepared.add(key)
    bridge.prepare(hash, name).catch(() => prepared.delete(key))
  }

  function startDrag(e: DragEvent, hash: string, name?: string): void {
    if (!bridge) return
    e.preventDefault()
    bridge.startDrag(hash, name)
  }

  async function saveBundle(stepId: string, preset?: string, name?: string, engine?: BundleEngine): Promise<void> {
    const dest = await run('bundle', () => bridge!.saveBundle(stepId, preset, name, engine), 'Không xuất được thư mục')
    if (dest) toasts.push(`Đã xuất thư mục ${baseName(dest)}`, 'success', 3200)
  }

  async function revealBundle(stepId: string, preset?: string, name?: string, engine?: BundleEngine): Promise<void> {
    await run('bundle', () => bridge!.revealBundle(stepId, preset, name, engine), 'Không mở được thư mục')
  }

  return { available: !!bridge, busy, copied, saveAs, reveal, copyPath, prepare, startDrag, saveBundle, revealBundle }
}
