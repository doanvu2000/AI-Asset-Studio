import { existsSync } from 'node:fs'
import { realpath } from 'node:fs/promises'
import { isAbsolute, join, relative } from 'node:path'
import type { CoreSupervisor } from './core'
import type { BundleEngine, BundleOptions, ExportedAsset, ExportedBundle } from '../preload/types'

const HASH_RE = /^[0-9a-f]{64}$/
const STEP_RE = /^[\w-]{1,80}$/
const PRESET_RE = /^[a-z0-9][a-z0-9_-]{0,47}$/

export function checkStep(v: unknown): string {
  if (typeof v !== 'string' || !STEP_RE.test(v)) throw new Error('invalid step id')
  return v
}

export function checkPreset(v: unknown): string | undefined {
  if (v === undefined || v === null || v === '') return undefined
  if (typeof v !== 'string' || !PRESET_RE.test(v)) throw new Error('invalid preset id')
  return v
}
export function checkEngine(v: unknown): BundleEngine | undefined {
  if (v === undefined || v === null || v === '') return undefined
  if (v !== 'unity' && v !== 'godot') throw new Error('invalid engine')
  return v
}
const NAME_MAX = 200
const CACHE_MAX = 64

export function checkHash(v: unknown): string {
  if (typeof v !== 'string' || !HASH_RE.test(v)) throw new Error('invalid blob hash')
  return v
}

export function checkName(v: unknown): string | undefined {
  if (v === undefined || v === null || v === '') return undefined
  if (typeof v !== 'string' || v.length > NAME_MAX) throw new Error('invalid file name')
  return v
}

async function inside(root: string, file: string): Promise<boolean> {
  const [r, f] = await Promise.all([realpath(root), realpath(file)])
  const rel = relative(r, f)
  return !!rel && !rel.startsWith('..') && !isAbsolute(rel)
}

/** Asks the core for a readable-name copy of a blob; only paths it returns under <data>/exports/app ever leave main. */
export class AssetExporter {
  private readonly cache = new Map<string, ExportedAsset>()

  constructor(private readonly core: CoreSupervisor) {}

  get root(): string {
    return join(this.core.dataDir, 'exports', 'app')
  }

  cached(hash: string, name?: string): ExportedAsset | undefined {
    const hit = this.cache.get(`${hash}\n${name ?? ''}`)
    return hit && existsSync(hit.path) ? hit : undefined
  }

  private async call<T>(path: string, body?: unknown, timeoutMs = 30_000): Promise<T> {
    const c = this.core.connection
    if (!c) throw new Error('Core chưa sẵn sàng')
    if (c.baseUrl.startsWith('mock:')) throw new Error('Bản mock không có tệp thật để xuất')
    const res = await fetch(`${c.baseUrl}/api/${path}`, {
      method: body === undefined ? 'GET' : 'POST',
      headers: { Authorization: `Bearer ${c.token}`, 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: AbortSignal.timeout(timeoutMs)
    })
    if (!res.ok) {
      let msg = `core trả lỗi ${res.status}`
      try {
        const err = (await res.json()) as { error?: { message?: string } }
        msg = err.error?.message ?? msg
      } catch {
        /* non-JSON error body */
      }
      throw new Error(msg)
    }
    return (await res.json()) as T
  }

  bundleOptions(stepId: string): Promise<BundleOptions> {
    return this.call<BundleOptions>(`steps/${stepId}/export`)
  }

  async exportBundle(stepId: string, preset?: string, name?: string, engine?: BundleEngine): Promise<ExportedBundle> {
    const body = { preset: preset ?? null, name: name ?? null, engine: engine ?? null }
    const out = await this.call<ExportedBundle>(`steps/${stepId}/export`, body, 120_000)
    if (typeof out.dir !== 'string' || !(await inside(this.root, out.dir))) throw new Error('core trả đường dẫn ngoài thư mục xuất')
    return { dir: out.dir, name: String(out.name), preset: out.preset ?? null, files: Array.isArray(out.files) ? out.files.length : 0 }
  }

  async export(hash: string, name?: string): Promise<ExportedAsset> {
    const key = `${hash}\n${name ?? ''}`
    const hit = this.cache.get(key)
    if (hit && existsSync(hit.path)) return hit
    const out = await this.call<ExportedAsset>(`blobs/${hash}/export`, { name: name ?? null })
    if (typeof out.path !== 'string' || !(await inside(this.root, out.path))) throw new Error('core trả đường dẫn ngoài thư mục xuất')
    const asset: ExportedAsset = { hash, path: out.path, name: String(out.name), mime: String(out.mime), size: Number(out.size) }
    if (this.cache.size >= CACHE_MAX) this.cache.delete(this.cache.keys().next().value!)
    this.cache.set(key, asset)
    return asset
  }
}
