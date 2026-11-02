import { ApiError } from '../client'
import type {
  BlobRef,
  EventType,
  FlowIssue,
  FlowValidateResult,
  RecipeInfo,
  RunRow,
  SavedWorkflow,
  StartRunBody,
  StepRow,
  WorkflowChild,
  WorkflowDef,
  WorkflowNodeState,
  WorkflowNodeStatus,
  WorkflowRunDetail,
  WorkflowRunRow,
  WorkflowStatus
} from '../types'
import { STEP_DONE_OK } from '../types'
import { isBlobType, nodeDeps, parseSource } from '@/composables/flows'

export interface FlowsHost {
  emit(type: EventType, data: unknown): void
  startRun(body: StartRunBody): Promise<{ run_id: string }>
  cancelRun(id: string): Promise<void>
  runRow(id: string): RunRow | undefined
  stepsOf(id: string): StepRow[]
  previewHash(id: string): string | null
  recipe(id: string): { info: RecipeInfo; outputs: string[] } | undefined
  makeBlob(seed: number): Promise<BlobRef>
  sampleRunIds(): string[]
}

const now = (): number => Date.now() / 1000
const uid = (): string => Math.random().toString(36).slice(2, 12) + Math.random().toString(36).slice(2, 8)
const clone = <T>(v: T): T => structuredClone(v)
const ID = /^[A-Za-z_][\w-]*$/
const LIVE: ReadonlySet<WorkflowNodeStatus> = new Set(['running', 'awaiting_human'])

const node = (id: string, recipe_id: string, title: string, map: Record<string, string>): WorkflowDef['nodes'][number] => ({ id, recipe_id, title, inputs: {}, map, params: {} })

const BUILTINS: WorkflowDef[] = [
  {
    id: 'concept-to-sprite',
    title: 'Concept → Sprite 8 hướng',
    description: 'Prompt → concept 2D (chọn 1 ảnh, tách nền) → sprite 8 hướng từ ảnh đã chọn.',
    inputs: { prompt: { type: 'text', label: 'Mô tả nhân vật', required: true } },
    nodes: [
      node('concept', 'concept-2d', 'Concept', { prompt: 'workflow.inputs.prompt' }),
      node('sprite', 'sprite-8dir', 'Sprite 8 hướng', { ref: 'concept.outputs.final[0]' })
    ],
    tags: ['2d', 'sprite'],
    source: 'builtin'
  },
  {
    id: 'character-pack',
    title: 'Bộ nhân vật đầy đủ (mẫu)',
    description: 'Từ một mô tả tạo concept, sprite, prop 3D, texture, hoạt ảnh và bản pixel art.',
    inputs: { prompt: { type: 'text', label: 'Mô tả nhân vật', required: true } },
    nodes: [
      node('concept', 'concept-2d', 'Concept nhân vật', { prompt: 'workflow.inputs.prompt' }),
      node('sprite', 'sprite-8dir', 'Sprite 8 hướng từ concept', { ref: 'concept.outputs.final[0]' }),
      node('prop', 'prop-3d', 'Mô hình 3D', { prompt: 'concept.inputs.prompt' }),
      node('tex', 'texture-seamless', 'Texture nền liền mạch', { prompt: 'concept.inputs.prompt' }),
      node('anim', 'sprite-8dir-anim', 'Hoạt ảnh đi bộ 8 hướng', { prompt: 'prop.inputs.prompt' }),
      node('pixel', 'sprite-8dir-3d-pixel', 'Sprite pixel art', { prompt: 'prop.inputs.prompt' }),
      node('spine', 'spine-character', 'Nhân vật Spine', { prompt: 'tex.inputs.prompt' })
    ],
    tags: ['2d', '3d'],
    source: 'builtin'
  }
]

export class MockFlows {
  private builtins = new Map(BUILTINS.map((d) => [d.id, d]))
  private users = new Map<string, WorkflowDef>()
  private runs = new Map<string, WorkflowRunRow>()
  private fake = new Map<string, Record<string, { progress: number | null; preview: string | null }>>()
  private demoId = ''

  constructor(private host: FlowsHost) {}

  async seed(): Promise<void> {
    const def = this.builtins.get('character-pack')!
    const runIds = this.host.sampleRunIds()
    const [a, b, c] = await Promise.all([this.host.makeBlob(4101), this.host.makeBlob(4207), this.host.makeBlob(4311)])
    const t = now() - 140
    const st = (status: WorkflowNodeStatus, run_id: string | null = null, error: string | null = null, attempts = 1): WorkflowNodeState => ({ status, run_id, error, attempts })
    const row: WorkflowRunRow = {
      id: 'demo-flow-run',
      workflow_id: def.id,
      title: 'Hiệp sĩ rồng lửa (xem mẫu)',
      project_id: 'default',
      status: 'running',
      def_snapshot: clone(def),
      inputs: { prompt: 'hiệp sĩ mặc giáp đỏ cầm kiếm lửa' },
      nodes: {
        concept: st('succeeded', runIds[0] ?? null),
        sprite: st('running', null, null, 2),
        prop: st('failed', runIds[1] ?? null, 'Blender thoát bất ngờ khi dọn mesh (comfy_node_errors)', 2),
        tex: st('cancelled', null, null, 1),
        anim: st('pending'),
        pixel: st('blocked'),
        spine: st('awaiting_human', runIds[2] ?? null)
      },
      error: null,
      created_at: t,
      updated_at: now(),
      ended_at: null
    }
    this.runs.set(row.id, row)
    this.demoId = row.id
    this.fake.set(row.id, {
      concept: { progress: 1, preview: a.hash },
      sprite: { progress: 0.62, preview: b.hash },
      prop: { progress: 0.4, preview: null },
      spine: { progress: 0.5, preview: c.hash }
    })
    window.setInterval(() => void this.tick(), 800)
  }

  // ---- definitions ----

  list(): WorkflowDef[] {
    const out = new Map<string, WorkflowDef>()
    for (const [id, d] of this.builtins) out.set(id, clone(d))
    for (const [id, d] of this.users) out.set(id, clone({ ...d, source: this.builtins.has(id) ? 'override' : 'user' }))
    return [...out.values()]
  }

  get(id: string): WorkflowDef {
    const d = this.list().find((x) => x.id === id)
    if (!d) throw new ApiError('not_found', `unknown workflow: ${id}`, 404)
    return d
  }

  validate(d: WorkflowDef): FlowValidateResult {
    const issues: FlowIssue[] = []
    const err = (code: string, message: string, node: string | null, field: string | null, params: Record<string, unknown> = {}): number =>
      issues.push({ level: 'error', node, field, code, params, message })
    if (!ID.test(d.id)) err('invalid_flow_id', `invalid workflow id '${d.id}'`, null, 'id', { id: d.id })
    if (!d.title.trim()) err('title_empty', 'title must not be empty', null, 'title')
    if (!d.nodes.length) err('no_nodes', 'a workflow needs at least one node', null, 'nodes')
    const ids = d.nodes.map((n) => n.id)
    for (const n of d.nodes) {
      if (!ID.test(n.id) || n.id === 'workflow') err('invalid_node_id', `invalid node id '${n.id}'`, n.id, 'id', { id: n.id })
      if (ids.indexOf(n.id) !== ids.lastIndexOf(n.id)) err('duplicate_node_id', `duplicate node id '${n.id}'`, n.id, 'id', { id: n.id })
      const r = this.host.recipe(n.recipe_id)
      if (!r) {
        err('unknown_recipe', `unknown recipe '${n.recipe_id}'`, n.id, 'recipe_id', { recipe: n.recipe_id })
        continue
      }
      for (const [target, src] of Object.entries(n.map)) {
        const p = parseSource(src)
        const tt = r.info.inputs[target]?.type
        if (!tt) err('map_target_unknown', `map target '${target}' is not an input of ${r.info.id}`, n.id, `map.${target}`, { target, recipe: r.info.id })
        else if (!p) err('bad_source', `map.${target}: bad source '${src}'`, n.id, `map.${target}`, { target, source: src })
        else if (p.ref === 'workflow') {
          const spec = d.inputs[p.name]
          if (!spec) err('workflow_no_input', `${src}: workflow has no input '${p.name}'`, n.id, `map.${target}`, { source: src, name: p.name })
          else if (isBlobType(spec.type) !== isBlobType(tt)) err('type_mismatch', `${src} is ${spec.type} but '${target}' is ${tt}`, n.id, `map.${target}`, { source: src, source_type: spec.type, target, target_type: tt })
        } else if (p.ref === n.id) err('self_feed', `${src}: a node cannot feed itself`, n.id, `map.${target}`, { source: src })
        else if (!ids.includes(p.ref)) err('unknown_node', `${src}: unknown node '${p.ref}'`, n.id, `map.${target}`, { source: src, ref: p.ref })
        else if (p.kind === 'outputs') {
          const outs = this.host.recipe(d.nodes.find((x) => x.id === p.ref)!.recipe_id)?.outputs ?? []
          if (outs.length && !outs.includes(p.name)) err('recipe_no_output', `${src}: no output '${p.name}' (has: ${outs.join(', ')})`, n.id, `map.${target}`, { source: src, name: p.name, outputs: outs })
          else if (!isBlobType(tt)) err('outputs_need_file', `'${target}' is ${tt}; only file inputs take a node's outputs`, n.id, `map.${target}`, { target, target_type: tt })
        }
      }
      for (const [name, spec] of Object.entries(r.info.inputs)) {
        if (spec.required !== false && spec.default === undefined && !(name in n.map) && (n.inputs[name] ?? '') === '')
          err('required_unset', `required input '${name}' of ${r.info.id} is neither set nor mapped`, n.id, `inputs.${name}`, { name, recipe: r.info.id })
      }
    }
    const deps = new Map(d.nodes.map((n) => [n.id, nodeDeps(n)]))
    const visiting = new Set<string>()
    const done = new Set<string>()
    const cyc = (id: string): boolean => {
      if (done.has(id)) return false
      if (visiting.has(id)) return true
      visiting.add(id)
      const hit = (deps.get(id) ?? []).some(cyc)
      visiting.delete(id)
      done.add(id)
      return hit
    }
    if (ids.some(cyc)) err('cycle', 'nodes form a cycle', null, 'nodes', { nodes: [] })
    return { ok: !issues.some((i) => i.level === 'error'), issues }
  }

  save(d: WorkflowDef, create: boolean): SavedWorkflow {
    if (create && this.users.has(d.id)) throw new ApiError('conflict', `workflow '${d.id}' already exists; use PUT to replace it`, 409)
    const res = this.validate(d)
    if (!res.ok) throw new ApiError('invalid', 'workflow is not valid', 400, [], { issues: res.issues })
    const saved = clone({ ...d, source: this.builtins.has(d.id) ? ('override' as const) : ('user' as const) })
    this.users.set(d.id, saved)
    return { ...clone(saved), issues: res.issues }
  }

  remove(id: string): { ok: boolean; restored_builtin: boolean } {
    if (!this.users.has(id)) {
      if (this.builtins.has(id)) throw new ApiError('invalid', `'${id}' is a built-in workflow and cannot be deleted`, 400)
      throw new ApiError('not_found', `unknown workflow: ${id}`, 404)
    }
    this.users.delete(id)
    return { ok: true, restored_builtin: this.builtins.has(id) }
  }

  // ---- runs ----

  async start(id: string, body: { inputs: Record<string, unknown>; project_id?: string; title?: string }): Promise<WorkflowRunRow> {
    const def = this.get(id)
    if (!this.validate(def).ok) throw new ApiError('invalid', 'workflow is not valid', 400)
    const inputs: Record<string, unknown> = {}
    for (const [name, spec] of Object.entries(def.inputs)) {
      const v = body.inputs[name]
      if (v === undefined || v === null || v === '') {
        if (spec.default == null && spec.required !== false) throw new ApiError('invalid', `input '${name}' is required`, 400)
        inputs[name] = spec.default ?? null
      } else inputs[name] = v
    }
    const t = now()
    const row: WorkflowRunRow = {
      id: `wf_${uid()}`,
      workflow_id: def.id,
      title: body.title || def.title,
      project_id: body.project_id ?? 'default',
      status: 'queued',
      def_snapshot: clone(def),
      inputs,
      nodes: Object.fromEntries(def.nodes.map((n) => [n.id, { status: 'pending', run_id: null, error: null, attempts: 0 } as WorkflowNodeState])),
      error: null,
      created_at: t,
      updated_at: t,
      ended_at: null
    }
    this.runs.set(row.id, row)
    this.host.emit('workflow.updated', row)
    await this.step(row)
    return clone(row)
  }

  list_runs(q: { workflow_id?: string; project_id?: string; limit?: number }): WorkflowRunRow[] {
    return [...this.runs.values()]
      .filter((r) => (!q.workflow_id || r.workflow_id === q.workflow_id) && (!q.project_id || r.project_id === q.project_id))
      .sort((a, b) => b.created_at - a.created_at)
      .slice(0, q.limit ?? 50)
      .map(clone)
  }

  detail(id: string): WorkflowRunDetail {
    const row = this.runs.get(id)
    if (!row) throw new ApiError('not_found', `unknown workflow run: ${id}`, 404)
    const fake = this.fake.get(id)
    const children: WorkflowChild[] = (row.def_snapshot.nodes ?? []).map((n) => {
      const ns = row.nodes[n.id] ?? { status: 'pending', run_id: null, error: null, attempts: 0 }
      const extra = fake?.[n.id]
      let progress: number | null = extra?.progress ?? null
      let preview = extra?.preview ?? null
      if (!fake && ns.run_id) {
        const steps = this.host.stepsOf(ns.run_id)
        progress = steps.length ? steps.reduce((a, s) => a + (STEP_DONE_OK.has(s.status) ? 1 : (s.progress ?? 0)), 0) / steps.length : null
        preview = this.host.previewHash(ns.run_id)
      }
      return { node_id: n.id, recipe_id: n.recipe_id, title: n.title || n.id, status: ns.status, run_id: ns.run_id, error: ns.error, attempts: ns.attempts, progress, preview }
    })
    return clone({ ...row, children })
  }

  async retry(id: string, nodeId: string): Promise<WorkflowRunRow> {
    const row = this.runs.get(id)
    const d = row?.def_snapshot as WorkflowDef | undefined
    if (!row || !d) throw new ApiError('not_found', `unknown workflow run: ${id}`, 404)
    const ns = row.nodes[nodeId]
    if (!ns) throw new ApiError('not_found', `workflow run ${id} has no node ${nodeId}`, 404)
    if (ns.status !== 'failed' && ns.status !== 'cancelled') throw new ApiError('bad_state', `node ${nodeId} is ${ns.status}; only failed/cancelled nodes can be retried`, 409)
    const reset = new Set([nodeId])
    for (let size = 0; size !== reset.size; ) {
      size = reset.size
      for (const n of d.nodes) if (nodeDeps(n).some((x) => reset.has(x))) reset.add(n.id)
    }
    for (const [k, s] of Object.entries(row.nodes)) if (s.status === 'cancelled') reset.add(k)
    for (const k of reset) row.nodes[k] = { status: 'pending', run_id: null, error: null, attempts: row.nodes[k]!.attempts }
    row.status = 'running'
    row.error = null
    row.ended_at = null
    if (id === this.demoId) {
      row.nodes[nodeId] = { status: 'running', run_id: null, error: null, attempts: ns.attempts + 1 }
      this.fake.get(id)![nodeId] = { progress: 0.1, preview: null }
      this.touch(row)
    } else await this.step(row)
    return clone(row)
  }

  async cancel(id: string): Promise<WorkflowRunRow> {
    const row = this.runs.get(id)
    if (!row) throw new ApiError('not_found', `unknown workflow run: ${id}`, 404)
    if (['succeeded', 'failed', 'cancelled'].includes(row.status)) return clone(row)
    row.status = 'cancelled'
    row.ended_at = now()
    for (const ns of Object.values(row.nodes)) {
      if (ns.run_id && LIVE.has(ns.status)) void this.host.cancelRun(ns.run_id).catch(() => undefined)
      if (!ns.run_id && (ns.status === 'pending' || ns.status === 'blocked' || ns.status === 'running')) ns.status = 'cancelled'
    }
    this.touch(row)
    return clone(row)
  }

  private touch(row: WorkflowRunRow): void {
    row.updated_at = now()
    this.host.emit('workflow.updated', row)
  }

  private async tick(): Promise<void> {
    for (const row of this.runs.values()) {
      if (row.id === this.demoId) this.tickDemo(row)
      else if (['queued', 'running', 'awaiting_human'].includes(row.status)) await this.step(row)
    }
  }

  // The sample run keeps one node moving so the live map can be judged by eye without a core.
  private tickDemo(row: WorkflowRunRow): void {
    const fake = this.fake.get(row.id)!
    const cur = fake.sprite!
    const next = (cur.progress ?? 0) + 0.04
    cur.progress = next > 0.96 ? 0.05 : next
    this.touch(row)
  }

  private async step(row: WorkflowRunRow): Promise<void> {
    const d = row.def_snapshot as WorkflowDef
    if (row.status === 'cancelled') return
    let changed = false
    for (const n of d.nodes) {
      const ns = row.nodes[n.id]!
      if (!ns.run_id) continue
      const r = this.host.runRow(ns.run_id)
      if (!r) continue
      const next: WorkflowNodeStatus =
        r.status === 'running' ? 'running' : r.status === 'awaiting_human' ? 'awaiting_human' : r.status === 'succeeded' ? 'succeeded' : r.status === 'cancelled' ? 'cancelled' : 'failed'
      if (next !== ns.status) {
        ns.status = next
        ns.error = next === 'failed' ? (r.error ?? 'failed') : null
        changed = true
      }
    }
    for (const n of d.nodes) {
      const ns = row.nodes[n.id]!
      if (ns.status !== 'pending' && ns.status !== 'blocked') continue
      const deps = nodeDeps(n).map((x) => row.nodes[x]!)
      if (deps.some((x) => ['failed', 'cancelled', 'blocked'].includes(x.status))) {
        if (ns.status !== 'blocked') {
          ns.status = 'blocked'
          changed = true
        }
      } else if (deps.every((x) => x.status === 'succeeded')) {
        await this.launch(row, d, n.id)
        changed = true
      }
    }
    const states = Object.values(row.nodes).map((s) => s.status)
    const status: WorkflowStatus = states.includes('running') || states.includes('pending') ? 'running'
      : states.includes('awaiting_human') ? 'awaiting_human'
      : states.every((s) => s === 'succeeded') ? 'succeeded'
      : states.includes('failed') ? 'failed' : 'cancelled'
    if (status !== row.status) {
      row.status = status
      row.ended_at = ['succeeded', 'failed', 'cancelled'].includes(status) ? now() : null
      changed = true
    }
    if (changed) this.touch(row)
  }

  private async launch(row: WorkflowRunRow, d: WorkflowDef, nodeId: string): Promise<void> {
    const n = d.nodes.find((x) => x.id === nodeId)!
    const ns = row.nodes[nodeId]!
    const inputs: Record<string, unknown> = { ...n.inputs }
    for (const [target, src] of Object.entries(n.map)) {
      const p = parseSource(src)
      if (!p) continue
      if (p.ref === 'workflow') inputs[target] = row.inputs[p.name]
      else if (p.kind === 'inputs') inputs[target] = this.runInputs(row, p.ref)[p.name]
      else {
        const rid = row.nodes[p.ref]?.run_id
        const blobs = rid ? this.finalBlobs(rid) : []
        inputs[target] = blobs[p.index ?? 0]?.hash
      }
    }
    const body: StartRunBody = { recipe_id: n.recipe_id, inputs, title: n.title || n.id, project_id: row.project_id, params: { $meta: { workflow_run_id: row.id, workflow_node_id: n.id } } }
    try {
      const { run_id } = await this.host.startRun(body)
      Object.assign(ns, { status: 'running', run_id, error: null, attempts: ns.attempts + 1 })
    } catch (e) {
      Object.assign(ns, { status: 'failed', error: (e as Error).message, attempts: ns.attempts + 1 })
    }
  }

  private runInputs(row: WorkflowRunRow, nodeId: string): Record<string, unknown> {
    const rid = row.nodes[nodeId]?.run_id
    return (rid ? this.host.runRow(rid)?.inputs : undefined) ?? {}
  }

  private finalBlobs(runId: string): BlobRef[] {
    for (const s of [...this.host.stepsOf(runId)].reverse()) {
      const list = Object.values(s.outputs).flat()
      if (list.length) return list
    }
    return []
  }
}
