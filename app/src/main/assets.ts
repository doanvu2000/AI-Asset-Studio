import { existsSync } from 'node:fs'
import { realpath } from 'node:fs/promises'
import { isAbsolute, join, relative } from 'node:path'
import type { CoreSupervisor } from './core'
import type { BundleEngine, BundleOptions, EngineExportOptions, ExportedAsset, ExportedBundle, HitBinding, MobileTotals } from '../preload/types'

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

const SHADER_ID_RE = /^[a-z][a-z0-9_]{0,31}$/
export function checkEngineOptions(v: unknown): EngineExportOptions {
  if (v === undefined || v === null) return {}
  if (typeof v !== 'object') throw new Error('invalid export options')
  const { ppu, pixel, collider, collider_tolerance: tol, shaders, shader_params: params } = v as Record<string, unknown>
  const out: EngineExportOptions = {}
  if (ppu !== undefined && ppu !== null) {
    if (typeof ppu !== 'number' || !Number.isFinite(ppu) || ppu <= 0 || ppu > 10000) throw new Error('invalid ppu')
    out.ppu = ppu
  }
  if (pixel !== undefined && pixel !== null) {
    if (typeof pixel !== 'boolean') throw new Error('invalid pixel flag')
    out.pixel = pixel
  }
  if (collider !== undefined && collider !== null) {
    if (typeof collider !== 'boolean') throw new Error('invalid collider flag')
    out.collider = collider
  }
  if (tol !== undefined && tol !== null) {
    if (typeof tol !== 'number' || !Number.isFinite(tol) || tol < 0 || tol > 64) throw new Error('invalid collider tolerance')
    out.collider_tolerance = tol
  }
  if (shaders !== undefined && shaders !== null) {
    if (!Array.isArray(shaders) || shaders.length > 16 || !shaders.every((s) => typeof s === 'string' && SHADER_ID_RE.test(s)))
      throw new Error('invalid shader presets')
    out.shaders = shaders as string[]
  }
  if (params !== undefined && params !== null) {
    if (typeof params !== 'object' || Array.isArray(params)) throw new Error('invalid shader params')
    const clean: Record<string, Record<string, number | string>> = {}
    for (const [id, vals] of Object.entries(params as Record<string, unknown>)) {
      if (!SHADER_ID_RE.test(id) || typeof vals !== 'object' || vals === null || Array.isArray(vals)) throw new Error('invalid shader params')
      clean[id] = {}
      for (const [k, x] of Object.entries(vals as Record<string, unknown>)) {
        const okNum = typeof x === 'number' && Number.isFinite(x)
        if (!SHADER_ID_RE.test(k) || !(okNum || (typeof x === 'string' && x.length <= 16))) throw new Error('invalid shader params')
        clean[id][k] = x as number | string
      }
    }
    out.shader_params = clean
  }
  const { normal2d, normal2d_strength: nStrength } = v as Record<string, unknown>
  if (normal2d !== undefined && normal2d !== null) {
    if (typeof normal2d !== 'boolean') throw new Error('invalid normal2d flag')
    out.normal2d = normal2d
  }
  if (nStrength !== undefined && nStrength !== null) {
    if (typeof nStrength !== 'number' || !Number.isFinite(nStrength) || nStrength <= 0 || nStrength > 10) throw new Error('invalid normal2d strength')
    out.normal2d_strength = nStrength
  }
  const { mobile } = v as Record<string, unknown>
  if (mobile !== undefined && mobile !== null) {
    if (typeof mobile !== 'boolean') throw new Error('invalid mobile flag')
    out.mobile = mobile
  }
  const { aseprite } = v as Record<string, unknown>
  if (aseprite !== undefined && aseprite !== null) {
    if (typeof aseprite !== 'boolean') throw new Error('invalid aseprite flag')
    out.aseprite = aseprite
  }
  const { hits } = v as Record<string, unknown>
  if (hits !== undefined && hits !== null) out.hits = checkHits(hits)
  return out
}
const NAME_MAX = 200

const TRACK_RE = /^[A-Za-z_][\w]{0,63}$/
function checkHits(v: unknown): HitBinding {
  if (typeof v !== 'object' || v === null || Array.isArray(v)) throw new Error('invalid hit vfx')
  const { vfx_step, frames, points, size } = v as Record<string, unknown>
  const out: HitBinding = { vfx_step: checkStep(vfx_step) }
  const entries = (x: unknown): [string, unknown][] => {
    if (x === undefined || x === null) return []
    if (typeof x !== 'object' || Array.isArray(x) || Object.keys(x).length > 64) throw new Error('invalid hit vfx')
    return Object.entries(x as Record<string, unknown>).map(([k, val]) => {
      if (!TRACK_RE.test(k)) throw new Error('invalid hit track')
      return [k, val]
    })
  }
  const okNum = (n: unknown): n is number => typeof n === 'number' && Number.isFinite(n)
  const f = entries(frames)
  if (f.length) {
    out.frames = {}
    for (const [k, n] of f) {
      if (!Number.isInteger(n) || (n as number) < 0 || (n as number) > 1024) throw new Error('invalid hit frame')
      out.frames[k] = n as number
    }
  }
  const pts = entries(points)
  if (pts.length) {
    out.points = {}
    for (const [k, p] of pts) {
      if (!Array.isArray(p) || p.length !== 2 || !p.every(okNum)) throw new Error('invalid hit point')
      out.points[k] = [p[0] as number, p[1] as number]
    }
  }
  if (size !== undefined && size !== null) {
    if (!okNum(size) || size <= 0 || size > 4) throw new Error('invalid hit vfx size')
    out.size = size
  }
  return out
}
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

  async exportBundle(stepId: string, preset?: string, name?: string, engine?: BundleEngine, opts: EngineExportOptions = {}): Promise<ExportedBundle> {
    const body = { preset: preset ?? null, name: name ?? null, engine: engine ?? null, ...(engine ? opts : {}) }
    // Engine exports re-pack every sheet (pixel filter, normal maps), well past a plain copy on big runs.
    const out = await this.call<Omit<ExportedBundle, 'files' | 'mobile'> & { files: unknown; mobile?: { totals?: MobileTotals } | null }>(
      `steps/${stepId}/export`, body, engine ? 600_000 : 120_000)
    if (typeof out.dir !== 'string' || !(await inside(this.root, out.dir))) throw new Error('core trả đường dẫn ngoài thư mục xuất')
    const t = out.mobile?.totals
    const mobile = t && [t.files, t.file_bytes, t.gpu_before, t.gpu_after].every((n) => typeof n === 'number') ? t : null
    return { dir: out.dir, name: String(out.name), preset: out.preset ?? null, files: Array.isArray(out.files) ? out.files.length : 0, mobile }
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
