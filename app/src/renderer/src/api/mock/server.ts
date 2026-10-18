import { ApiError, type ListRunsQuery, type StudioApi } from '../client'
import { FrameBatcher, type EventSink, type EventStream } from '../ws'
import {
  STEP_DONE_OK,
  type BlobRef,
  type EventType,
  type GateResolution,
  type Lane,
  type Outputs,
  type RecipeInfo,
  type ResourceHealth,
  type RunDetail,
  type RunRow,
  type StartRunBody,
  type StepRow,
  type VersionRow,
  type VersionStatus
} from '../types'
import { renderArt, renderPreviewB64 } from './art'

interface MockStepDef {
  id: string
  kind: string
  lane: Lane
  deps: string[]
  params?: (inputs: Record<string, unknown>) => Record<string, unknown>
}

interface MockRecipe {
  info: RecipeInfo
  defs: MockStepDef[]
}

const LANE_LIMITS: Record<Lane, number> = { gpu: 1, blender: 1, cloud: 3, cpu: 4, none: Infinity }

const RECIPES: MockRecipe[] = [
  {
    info: {
      id: 'concept-2d',
      name: 'Concept 2D (Z-Image)',
      description: 'Text → N ảnh → chọn → tách nền → căn chỉnh',
      hash: 'b3c0ffee01',
      inputs: {
        prompt: { type: 'text', label: 'Prompt', required: true, help: 'Mô tả vật thể. Gõ "lỗi" để thử nhánh thất bại.' },
        count: { type: 'int', default: 4, min: 1, max: 8, label: 'Số biến thể' },
        size: { type: 'int', default: 512, options: [128, 256, 512, 1024], label: 'Kích thước' },
        seed: { type: 'int', default: -1, min: -1, label: 'Seed', help: '−1 là ngẫu nhiên. Seed cố định cho phép dùng lại kết quả cũ.' }
      },
      steps: []
    },
    defs: [
      { id: 'gen', kind: 'comfy.t2i_zimage_turbo', lane: 'gpu', deps: [], params: (i) => ({ '57:27.text': i.prompt, '57:13.batch_size': i.count, seed: i.seed }) },
      { id: 'pick', kind: 'gate.pick', lane: 'none', deps: ['gen'], params: () => ({ k: 1 }) },
      { id: 'rmbg', kind: 'comfy.remove_bg_birefnet', lane: 'gpu', deps: ['pick'] },
      { id: 'fit', kind: 'op.trim_pad_resize', lane: 'cpu', deps: ['rmbg'], params: (i) => ({ size: i.size, pad: 0.06 }) }
    ]
  },
  {
    info: {
      id: 'concept-2d-codex',
      name: 'Concept 2D (Codex)',
      description: 'Codex sinh ảnh nền trong suốt → chọn → căn chỉnh',
      hash: 'b3c0dex002',
      inputs: {
        prompt: { type: 'text', label: 'Prompt', required: true },
        count: { type: 'int', default: 2, min: 1, max: 4, label: 'Số biến thể' },
        transparent: { type: 'bool', default: true, label: 'Nền trong suốt' }
      },
      steps: []
    },
    defs: [
      { id: 'gen', kind: 'codex.image', lane: 'cloud', deps: [], params: (i) => ({ prompt: i.prompt, count: i.count }) },
      { id: 'pick', kind: 'gate.pick', lane: 'none', deps: ['gen'], params: () => ({ k: 1 }) },
      { id: 'fit', kind: 'op.trim_pad_resize', lane: 'cpu', deps: ['pick'], params: () => ({ size: 512, pad: 0.06 }) }
    ]
  },
  {
    info: {
      id: 'sprite-8dir',
      name: 'Sprite 8 hướng',
      description: 'Ảnh tham chiếu → mesh 3D → render 5 góc → lật thành 8 hướng → sheet',
      hash: 'b3c08d1r03',
      inputs: {
        ref: { type: 'image', label: 'Ảnh tham chiếu', required: true },
        elevation: { type: 'float', default: 30, min: 0, max: 60, step: 5, label: 'Góc nhìn (độ)' },
        res: { type: 'int', default: 256, options: [128, 256, 512], label: 'Độ phân giải' }
      },
      steps: []
    },
    defs: [
      { id: 'mesh', kind: 'comfy.i23d_hunyuan', lane: 'gpu', deps: [] },
      { id: 'render', kind: 'blender.render_dirs', lane: 'blender', deps: ['mesh'], params: (i) => ({ elevation: i.elevation, count: 5, res: i.res }) },
      { id: 'mirror', kind: 'op.mirror_dirs', lane: 'cpu', deps: ['render'], params: () => ({ count: 8 }) },
      { id: 'approve', kind: 'gate.approve', lane: 'none', deps: ['mirror'] },
      { id: 'sheet', kind: 'op.sheet', lane: 'cpu', deps: ['approve'], params: () => ({ cols: 8, cell: 256 }) }
    ]
  }
]
for (const r of RECIPES) r.info.steps = r.defs.map((d) => ({ id: d.id, kind: d.kind, lane: d.lane }))

const TITLES = ['Kiếm pha lê', 'Bình máu đỏ', 'Rương gỗ cổ', 'Nấm phát sáng', 'Khiên đồng', 'Đá rune', 'Cuộn phép', 'Lồng đèn giấy', 'Mũ phù thuỷ', 'Trứng rồng']

const sleep = (ms: number): Promise<void> => new Promise((r) => setTimeout(r, ms))
const now = (): number => Date.now() / 1000
const uid = (p: string): string => `${p}_${Math.random().toString(36).slice(2, 10)}`
const hex = (): string => Array.from(crypto.getRandomValues(new Uint8Array(32)), (b) => b.toString(16).padStart(2, '0')).join('')
const clone = <T>(v: T): T => structuredClone(v)

class Cancelled extends Error {}

export class MockCore implements StudioApi, EventStream {
  private seq = 0
  private sink: EventSink | null = null
  private batcher: FrameBatcher | null = null
  private blobs = new Map<string, { url: string; seed: number; ref: BlobRef }>()
  private runs = new Map<string, RunRow>()
  private steps = new Map<string, StepRow>()
  private versionMap = new Map<string, VersionRow>()
  private resourcesMap = new Map<string, ResourceHealth>()
  private cache = new Map<string, Outputs>()
  private laneBusy: Record<Lane, number> = { gpu: 0, blender: 0, cloud: 0, cpu: 0, none: 0 }
  private laneQueue: Record<Lane, Array<() => void>> = { gpu: [], blender: [], cloud: [], cpu: [], none: [] }
  private cancelled = new Set<string>()
  private ready: Promise<void>

  constructor() {
    this.ready = this.seed()
    window.setInterval(() => this.tickResources(), 1000)
  }

  start(sink: EventSink): void {
    this.sink = sink
    this.batcher = new FrameBatcher((events) => sink.batch(events))
    sink.state('connecting')
    void this.ready.then(() => {
      sink.state('open')
      sink.resync()
    })
  }

  stop(): void {
    this.sink?.state('closed')
    this.sink = null
  }

  private emit(type: EventType, data: unknown, run_id: string | null = null, step_id: string | null = null): void {
    this.seq += 1
    this.batcher?.push({ seq: this.seq, ts: now(), type, run_id, step_id, data: clone(data) as Record<string, unknown> })
  }

  // ---- blobs ----

  private async makeBlob(seed: number, opts: { size?: number; transparent?: boolean; name?: string }): Promise<BlobRef> {
    const size = opts.size ?? 512
    const blob = await renderArt({ seed, size, transparent: opts.transparent })
    const ref: BlobRef = { hash: hex(), mime: blob.type, size: blob.size, width: size, height: size, name: opts.name ?? `${seed}.${blob.type.split('/')[1]}` }
    this.blobs.set(ref.hash, { url: URL.createObjectURL(blob), seed, ref })
    return ref
  }

  private seedOf(hash: string): number {
    return this.blobs.get(hash)?.seed ?? 7
  }

  blobUrl(hash: string): string {
    return this.blobs.get(hash)?.url ?? ''
  }

  thumbUrl(hash: string): string {
    return this.blobUrl(hash)
  }

  async uploadBlob(file: File): Promise<BlobRef> {
    await sleep(350)
    const ref: BlobRef = { hash: hex(), mime: file.type || 'application/octet-stream', size: file.size, name: file.name }
    this.blobs.set(ref.hash, { url: URL.createObjectURL(file), seed: Math.floor(Math.random() * 1e6), ref })
    return ref
  }

  // ---- seed data ----

  private async seed(): Promise<void> {
    this.resourcesMap.set('comfyui', { name: 'comfyui', status: 'up', detail: { vram_free_mb: 13800, vram_total_mb: 16376, version: '0.3.68', managed: true, pid: 18244 } })
    this.resourcesMap.set('blender', { name: 'blender', status: 'down', detail: { version: '4.5.3', managed: true } })
    this.resourcesMap.set('codex', { name: 'codex', status: 'up', detail: { version: 'codex-cli 0.48' } })
    this.resourcesMap.set('claude', { name: 'claude', status: 'up', detail: { version: 'claude 2.1' } })

    const pool: BlobRef[] = []
    for (let i = 0; i < 18; i++) pool.push(await this.makeBlob(1000 + i * 37, { size: 256, transparent: true }))

    const statuses: RunRow['status'][] = ['succeeded', 'succeeded', 'succeeded', 'failed', 'succeeded', 'cancelled', 'interrupted', 'succeeded']
    const t0 = now()
    for (let i = 0; i < 64; i++) {
      const recipe = RECIPES[i % 5 === 0 ? 1 : 0]!
      const status = statuses[i % statuses.length]!
      const created = t0 - 3600 * (i + 1) - Math.random() * 1800
      const run = this.newRun(recipe, { prompt: TITLES[i % TITLES.length], count: 4, size: 512, seed: -1 }, TITLES[i % TITLES.length]!, created)
      run.status = status
      run.ended_at = created + 40 + Math.random() * 90
      if (status === 'failed') run.error = 'comfy_node_errors'
      const steps = this.createSteps(run, recipe)
      let t = created
      for (const s of steps) {
        const img = pool[(i + s.order) % pool.length]!
        const failedHere = status === 'failed' && s.key === 'rmbg'
        const interruptedHere = status === 'interrupted' && s.order === 0
        const cancelledHere = status === 'cancelled' && s.order === 2
        const prior = steps.filter((x) => x.order < s.order)
        const blocked = prior.some((x) => !STEP_DONE_OK.has(x.status))
        s.started_at = t
        t += 5 + Math.random() * 20
        if (blocked) {
          s.status = 'skipped'
          s.started_at = null
        } else if (failedHere) {
          s.status = 'failed'
          s.error = { code: 'comfy_node_errors', message: 'BiRefNet: CUDA out of memory khi tách nền (cần ~2.1 GB VRAM trống).' }
          s.ended_at = t
        } else if (interruptedHere) {
          s.status = 'failed'
          s.error = { code: 'interrupted', message: 'Core khởi động lại khi bước đang chạy.', retryable: true }
          s.ended_at = t
        } else if (cancelledHere) {
          s.status = 'cancelled'
          s.ended_at = t
        } else {
          s.status = s.key === 'fit' && i % 3 === 0 ? 'cached' : 'succeeded'
          s.ended_at = t
          s.outputs = { images: s.key === 'gen' ? [img, pool[(i + 5) % pool.length]!, pool[(i + 9) % pool.length]!] : [img] }
        }
      }
      if (status === 'succeeded') {
        const fit = steps[steps.length - 1]!
        for (const b of fit.outputs.images ?? []) {
          const v: VersionRow = { id: uid('ver'), project_id: 'default', asset_name: run.title, run_id: run.id, step_id: fit.id, blob: b, status: i % 4 === 0 ? 'draft' : i % 7 === 0 ? 'rejected' : 'approved', created_at: run.ended_at! }
          this.versionMap.set(v.id, v)
        }
      }
    }

    // A live run occupying the GPU so a new run visibly waits for the lane, plus one gate waiting for a pick.
    void this.startRun({ recipe_id: 'concept-2d', inputs: { prompt: 'Kiếm pha lê xanh, low-poly', count: 4, size: 512, seed: -1 }, title: 'Kiếm pha lê xanh' })
    const gateRun = await this.startRun({ recipe_id: 'concept-2d-codex', inputs: { prompt: 'Bình thuốc tím phát sáng', count: 4, transparent: true }, title: 'Bình thuốc tím' })
    void gateRun
  }

  private newRun(recipe: MockRecipe, inputs: Record<string, unknown>, title: string, created = now()): RunRow {
    const run: RunRow = { id: uid('run'), project_id: 'default', recipe_id: recipe.info.id, recipe_hash: recipe.info.hash, title, inputs, params: {}, status: 'running', error: null, created_at: created, ended_at: null }
    this.runs.set(run.id, run)
    return run
  }

  private createSteps(run: RunRow, recipe: MockRecipe): StepRow[] {
    return recipe.defs.map((d, order) => {
      const s: StepRow = { id: uid('step'), run_id: run.id, key: d.id, kind: d.kind, lane: d.lane, status: 'pending', attempt: 1, deps: d.deps, params: d.params?.(run.inputs) ?? {}, inputs: {}, outputs: {}, cache_key: null, error: null, progress: null, meta: {}, order, started_at: null, ended_at: null }
      this.steps.set(s.id, s)
      return s
    })
  }

  private stepsOf(runId: string): StepRow[] {
    return [...this.steps.values()].filter((s) => s.run_id === runId).sort((a, b) => a.order - b.order)
  }

  private patchStep(s: StepRow, patch: Partial<StepRow>): void {
    Object.assign(s, patch)
    this.emit('step.updated', s, s.run_id, s.id)
  }

  private patchRun(r: RunRow, patch: Partial<RunRow>): void {
    Object.assign(r, patch)
    this.emit('run.updated', { status: r.status, error: r.error, ended_at: r.ended_at }, r.id)
  }

  // ---- lanes ----

  private tryAcquire(lane: Lane): boolean {
    if (this.laneBusy[lane] < LANE_LIMITS[lane]) {
      this.laneBusy[lane] += 1
      return true
    }
    return false
  }

  private acquire(lane: Lane): Promise<void> {
    return new Promise((resolve) => this.laneQueue[lane].push(() => resolve()))
  }

  private release(lane: Lane): void {
    const next = this.laneQueue[lane].shift()
    if (next) next()
    else this.laneBusy[lane] -= 1
  }

  // ---- engine ----

  private advance(runId: string): void {
    const run = this.runs.get(runId)
    if (!run || !['running', 'awaiting_human'].includes(run.status)) return
    const steps = this.stepsOf(runId)
    const byKey = new Map(steps.map((s) => [s.key, s]))

    if (steps.some((s) => s.status === 'failed')) {
      for (const s of steps) if (s.status === 'pending') this.patchStep(s, { status: 'skipped' })
      if (!steps.some((s) => ['running', 'waiting_resource', 'ready'].includes(s.status))) {
        const failed = steps.find((s) => s.status === 'failed')!
        this.patchRun(run, { status: 'failed', error: failed.error?.code ?? 'failed', ended_at: now() })
      }
      return
    }

    for (const s of steps) {
      if (s.status !== 'pending') continue
      if (s.deps.every((d) => STEP_DONE_OK.has(byKey.get(d)?.status ?? 'pending'))) {
        s.inputs = { images: s.deps.flatMap((d) => byKey.get(d)?.outputs.images ?? []) }
        void this.execStep(s)
      }
    }

    const active = steps.some((s) => ['ready', 'waiting_resource', 'running'].includes(s.status))
    const awaiting = steps.some((s) => s.status === 'awaiting_human')
    if (steps.every((s) => STEP_DONE_OK.has(s.status))) {
      this.patchRun(run, { status: 'succeeded', ended_at: now() })
      const last = steps[steps.length - 1]!
      for (const b of last.outputs.images ?? last.outputs.sheet ?? []) {
        const v: VersionRow = { id: uid('ver'), project_id: 'default', asset_name: run.title || run.recipe_id, run_id: run.id, step_id: last.id, blob: b, status: 'draft', created_at: now() }
        this.versionMap.set(v.id, v)
        this.emit('version.updated', v, run.id)
      }
    } else if (!active && awaiting && run.status !== 'awaiting_human') {
      this.patchRun(run, { status: 'awaiting_human' })
    } else if (active && run.status === 'awaiting_human') {
      this.patchRun(run, { status: 'running' })
    }
  }

  private cacheKey(s: StepRow): string | null {
    const run = this.runs.get(s.run_id)!
    if (s.lane === 'none' || s.kind.startsWith('codex.')) return null
    if (s.key === 'gen' && Number(run.inputs.seed ?? -1) < 0) return null
    const inputs = (s.inputs.images ?? []).map((b) => b.hash).sort().join(',')
    return `${s.kind}|${JSON.stringify(s.params)}|${inputs}`
  }

  private checkCancel(s: StepRow): void {
    if (this.cancelled.has(s.run_id)) throw new Cancelled()
  }

  private async execStep(s: StepRow): Promise<void> {
    this.patchStep(s, { status: 'ready' })
    await sleep(180)
    if (this.cancelled.has(s.run_id)) return

    const key = this.cacheKey(s)
    if (key && this.cache.has(key)) {
      this.patchStep(s, { status: 'cached', cache_key: key, outputs: clone(this.cache.get(key)!), started_at: now(), ended_at: now(), progress: 1 })
      this.advance(s.run_id)
      return
    }

    if (s.lane === 'none') {
      const candidates = s.inputs.images ?? []
      const kind = s.kind === 'gate.pick' ? 'pick' : 'approve'
      this.patchStep(s, { status: 'awaiting_human', started_at: now(), meta: { gate: kind } })
      this.emit('gate.awaiting', { kind, k: kind === 'pick' ? Number(s.params.k ?? 0) : 0, candidates }, s.run_id, s.id)
      this.advance(s.run_id)
      return
    }

    if (!this.tryAcquire(s.lane)) {
      this.patchStep(s, { status: 'waiting_resource', meta: { waiting_for: s.lane } })
      await this.acquire(s.lane)
    }
    try {
      this.checkCancel(s)
      this.patchStep(s, { status: 'running', started_at: now(), progress: 0, meta: {} })
      const outputs = await this.simulate(s)
      if (key) this.cache.set(key, outputs)
      this.patchStep(s, { status: 'succeeded', outputs, progress: 1, ended_at: now(), cache_key: key })
    } catch (e) {
      if (e instanceof Cancelled) {
        this.patchStep(s, { status: 'cancelled', ended_at: now() })
      } else {
        const err = e as { code?: string; message?: string }
        this.patchStep(s, { status: 'failed', ended_at: now(), error: { code: err.code ?? 'mock_error', message: err.message ?? String(e), retryable: true } })
      }
    } finally {
      this.release(s.lane)
    }
    this.advance(s.run_id)
  }

  private progress(s: StepRow, value: number, max: number, node: string, label?: string): void {
    s.progress = max ? value / max : null
    this.emit('step.progress', { value, max, node, label: label ?? null }, s.run_id, s.id)
  }

  private log(s: StepRow, msg: string, level = 'info'): void {
    this.emit('step.log', { level, msg }, s.run_id, s.id)
  }

  private async simulate(s: StepRow): Promise<Outputs> {
    const run = this.runs.get(s.run_id)!
    const inImgs = s.inputs.images ?? []
    const size = Number(run.inputs.size ?? run.inputs.res ?? 512)

    if (s.kind === 'comfy.t2i_zimage_turbo') {
      const count = Number(run.inputs.count ?? 4)
      const seedBase = Number(run.inputs.seed ?? -1) >= 0 ? Number(run.inputs.seed) : Math.floor(Math.random() * 1e6)
      this.progress(s, 0, 1, 'CLIPTextEncode', 'Mã hoá prompt')
      await sleep(600)
      this.checkCancel(s)
      const total = 9
      for (let i = 1; i <= total; i++) {
        await sleep(480)
        this.checkCancel(s)
        this.progress(s, i, total, 'KSampler', `Lấy mẫu ${i}/${total}`)
        void renderPreviewB64(seedBase, 1 - i / total).then((b64) => this.emit('step.preview', { mime: 'image/jpeg', b64 }, s.run_id, s.id))
      }
      this.progress(s, total, total, 'VAEDecode', 'Giải mã ảnh')
      await sleep(500)
      const out: BlobRef[] = []
      for (let i = 0; i < count; i++) out.push(await this.makeBlob(seedBase + i * 101, { size: 512 }))
      this.log(s, `Sinh ${count} ảnh, seed ${seedBase}`)
      return { images: out }
    }

    if (s.kind === 'codex.image') {
      const count = Number(run.inputs.count ?? 2)
      this.log(s, `codex exec: ${count} tiến trình song song`)
      await sleep(4200)
      this.checkCancel(s)
      const out: BlobRef[] = []
      for (let i = 0; i < count; i++) out.push(await this.makeBlob(Math.floor(Math.random() * 1e6), { size: 512, transparent: true }))
      return { images: out }
    }

    if (s.kind === 'comfy.remove_bg_birefnet') {
      const prompt = String(run.inputs.prompt ?? '').toLowerCase()
      const out: BlobRef[] = []
      for (let i = 0; i < inImgs.length; i++) {
        this.progress(s, i, inImgs.length, 'BiRefNet', `Tách nền ${i + 1}/${inImgs.length}`)
        await sleep(1100)
        this.checkCancel(s)
        if (s.attempt === 1 && (prompt.includes('lỗi') || prompt.includes('fail'))) {
          throw Object.assign(new Error('BiRefNet: CUDA out of memory khi tách nền (cần ~2.1 GB VRAM trống). Đóng ứng dụng khác dùng GPU rồi thử lại.'), { code: 'comfy_node_errors' })
        }
        out.push(await this.makeBlob(this.seedOf(inImgs[i]!.hash), { size: 512, transparent: true }))
      }
      this.progress(s, inImgs.length, inImgs.length, 'SaveImage')
      return { images: out }
    }

    if (s.kind === 'op.trim_pad_resize') {
      await sleep(450)
      const out: BlobRef[] = []
      for (const b of inImgs) out.push(await this.makeBlob(this.seedOf(b.hash), { size, transparent: true }))
      return { images: out }
    }

    if (s.kind === 'op.mirror_dirs') {
      await sleep(400)
      const out: BlobRef[] = []
      for (let i = 0; i < 8; i++) out.push(await this.makeBlob(this.seedOf(inImgs[i % Math.max(1, inImgs.length)]?.hash ?? '') + i, { size, transparent: true }))
      return { images: out }
    }

    const n = s.kind === 'blender.render_dirs' ? 5 : 1
    const steps = 6
    for (let i = 1; i <= steps; i++) {
      await sleep(500)
      this.checkCancel(s)
      this.progress(s, i, steps, s.kind.split('.')[1] ?? s.kind)
    }
    const out: BlobRef[] = []
    for (let i = 0; i < n; i++) out.push(await this.makeBlob(Math.floor(Math.random() * 1e6), { size, transparent: true }))
    return s.kind === 'op.sheet' ? { sheet: out } : { images: out }
  }

  private tickResources(): void {
    const comfy = this.resourcesMap.get('comfyui')
    if (!comfy || comfy.status === 'down') return
    const busy = this.laneBusy.gpu > 0
    const total = Number(comfy.detail.vram_total_mb)
    const target = busy ? total - 2400 - 9800 - Math.random() * 700 : total - 2400 - Math.random() * 300
    const next: ResourceHealth = { ...comfy, status: busy ? 'busy' : 'up', detail: { ...comfy.detail, vram_free_mb: Math.round(target) } }
    if (next.status !== comfy.status || Math.abs(Number(next.detail.vram_free_mb) - Number(comfy.detail.vram_free_mb)) > 60) {
      this.resourcesMap.set('comfyui', next)
      this.emit('resource.updated', next)
    }
  }

  // ---- API ----

  async health(): Promise<{ ok: boolean; version: string }> {
    return { ok: true, version: 'mock' }
  }

  async resources(): Promise<ResourceHealth[]> {
    await this.ready
    return clone([...this.resourcesMap.values()])
  }

  async startResource(name: string): Promise<ResourceHealth> {
    const r = this.resourcesMap.get(name)
    if (!r) throw new ApiError('not_found', `Không có tài nguyên ${name}`, 404)
    const starting: ResourceHealth = { ...r, status: 'starting' }
    this.resourcesMap.set(name, starting)
    this.emit('resource.updated', starting)
    window.setTimeout(() => {
      const up: ResourceHealth = { ...starting, status: 'up', detail: { ...starting.detail, pid: 20000 + Math.floor(Math.random() * 9000) } }
      this.resourcesMap.set(name, up)
      this.emit('resource.updated', up)
    }, 1600)
    return clone(starting)
  }

  async stopResource(name: string): Promise<ResourceHealth> {
    const r = this.resourcesMap.get(name)
    if (!r) throw new ApiError('not_found', `Không có tài nguyên ${name}`, 404)
    const down: ResourceHealth = { ...r, status: 'down', detail: { ...r.detail, pid: undefined } }
    this.resourcesMap.set(name, down)
    this.emit('resource.updated', down)
    return clone(down)
  }

  async recipes(): Promise<RecipeInfo[]> {
    await sleep(80)
    return clone(RECIPES.map((r) => r.info))
  }

  async startRun(body: StartRunBody): Promise<{ run_id: string }> {
    const recipe = RECIPES.find((r) => r.info.id === body.recipe_id)
    if (!recipe) throw new ApiError('unknown_recipe', `Không có recipe ${body.recipe_id}`, 400)
    const title = body.title || String(body.inputs.prompt ?? recipe.info.name).slice(0, 60)
    const run = this.newRun(recipe, body.inputs, title)
    const steps = this.createSteps(run, recipe)
    this.emit('run.created', run, run.id)
    for (const s of steps) this.emit('step.updated', s, run.id, s.id)
    window.setTimeout(() => this.advance(run.id), 150)
    return { run_id: run.id }
  }

  async listRuns(q: ListRunsQuery = {}): Promise<RunRow[]> {
    await this.ready
    let rows = [...this.runs.values()].sort((a, b) => b.created_at - a.created_at)
    if (q.status) rows = rows.filter((r) => r.status === q.status)
    if (q.before) rows = rows.filter((r) => r.created_at < q.before!)
    return clone(rows.slice(0, q.limit ?? 50))
  }

  async getRun(id: string): Promise<RunDetail> {
    await this.ready
    const run = this.runs.get(id)
    if (!run) throw new ApiError('not_found', 'Không tìm thấy run', 404)
    return clone({ run, steps: this.stepsOf(id) })
  }

  async cancelRun(id: string): Promise<void> {
    const run = this.runs.get(id)
    if (!run) throw new ApiError('not_found', 'Không tìm thấy run', 404)
    this.cancelled.add(id)
    for (const s of this.stepsOf(id)) {
      if (s.status === 'pending') this.patchStep(s, { status: 'skipped' })
      else if (['ready', 'waiting_resource', 'awaiting_human'].includes(s.status)) this.patchStep(s, { status: 'cancelled', ended_at: now() })
    }
    window.setTimeout(() => this.patchRun(run, { status: 'cancelled', ended_at: now() }), 300)
  }

  async retryStep(stepId: string): Promise<void> {
    const s = this.steps.get(stepId)
    if (!s || !['failed', 'cancelled'].includes(s.status)) throw new ApiError('bad_state', 'Chỉ thử lại được bước lỗi hoặc đã huỷ', 409)
    const run = this.runs.get(s.run_id)!
    this.cancelled.delete(run.id)
    const steps = this.stepsOf(run.id)
    const downstream = new Set<string>([s.key])
    for (const x of steps) if (x.deps.some((d) => downstream.has(d))) downstream.add(x.key)
    for (const x of steps) {
      if (x.id === s.id) this.patchStep(x, { status: 'pending', attempt: x.attempt + 1, error: null, progress: null, outputs: {}, started_at: null, ended_at: null })
      else if (downstream.has(x.key) || x.status === 'skipped') this.patchStep(x, { status: 'pending', error: null, progress: null, outputs: {}, started_at: null, ended_at: null })
    }
    this.patchRun(run, { status: 'running', error: null, ended_at: null })
    window.setTimeout(() => this.advance(run.id), 120)
  }

  async gates(): Promise<StepRow[]> {
    await this.ready
    return clone([...this.steps.values()].filter((s) => s.status === 'awaiting_human'))
  }

  async resolveGate(stepId: string, res: GateResolution): Promise<void> {
    const s = this.steps.get(stepId)
    if (!s || s.status !== 'awaiting_human') throw new ApiError('bad_state', 'Cổng duyệt không còn chờ', 409)
    const imgs = s.inputs.images ?? []
    if (s.kind === 'gate.pick') {
      const picked = imgs.filter((b) => res.picked?.includes(b.hash))
      if (!picked.length) throw new ApiError('invalid', 'Chọn ít nhất 1 ảnh', 400)
      this.patchStep(s, { status: 'succeeded', outputs: { images: picked }, ended_at: now() })
    } else if (res.approved) {
      this.patchStep(s, { status: 'succeeded', outputs: s.inputs, ended_at: now() })
    } else {
      this.patchStep(s, { status: 'failed', error: { code: 'rejected', message: 'Bị từ chối ở bước duyệt' }, ended_at: now() })
    }
    this.advance(s.run_id)
  }

  async versions(q: { run_id?: string; status?: VersionStatus } = {}): Promise<VersionRow[]> {
    await this.ready
    let rows = [...this.versionMap.values()].sort((a, b) => b.created_at - a.created_at)
    if (q.run_id) rows = rows.filter((v) => v.run_id === q.run_id)
    if (q.status) rows = rows.filter((v) => v.status === q.status)
    return clone(rows)
  }

  async setVersionStatus(id: string, action: 'approve' | 'reject'): Promise<VersionRow> {
    const v = this.versionMap.get(id)
    if (!v) throw new ApiError('not_found', 'Không tìm thấy phiên bản', 404)
    v.status = action === 'approve' ? 'approved' : 'rejected'
    this.emit('version.updated', v, v.run_id)
    return clone(v)
  }
}
