import type { Edge, Node } from '@vue-flow/core'
import type { Binding, KindInfo, Lane, RecipeDoc, RecipeIssue, RecipeStepDoc, StepRow } from '@/api/types'

export const INPUTS_NODE = '__inputs'
export const OUTPUTS_NODE = '__outputs'
export const NEW_PORT = '__new'
const COL_W = 300
const ROW_H = 150
const STEP_REF = /\$\{steps\.([A-Za-z_][\w-]*)\.([A-Za-z_][\w-]*)/g

export interface StepNodeData {
  mode: 'edit' | 'run' | 'peek'
  stepId: string
  kind: string
  lane: Lane
  label: string
  inPorts: string[]
  outPorts: string[]
  acceptsAny: boolean
  mapped: string | null
  issues: RecipeIssue[]
  selected: boolean
  order?: number
  vertical?: boolean
  step?: StepRow
}

export interface IoNodeData {
  role: 'inputs' | 'outputs'
  peek?: boolean
  ports: { name: string; type: string; bindable: boolean }[]
  selected: boolean
  issues: RecipeIssue[]
}

export function parts(b: Binding): string[] {
  return Array.isArray(b) ? b : [b]
}

export function splitRef(part: string): [string, string] {
  const i = part.indexOf('.')
  return [part.slice(0, i), part.slice(i + 1)]
}

export function stepRefs(params: unknown): Array<[string, string]> {
  const out: Array<[string, string]> = []
  const walk = (v: unknown): void => {
    if (typeof v === 'string') for (const m of v.matchAll(STEP_REF)) out.push([m[1]!, m[2]!])
    else if (Array.isArray(v)) v.forEach(walk)
    else if (v && typeof v === 'object') Object.values(v).forEach(walk)
  }
  walk(params)
  return out
}

export function stepDeps(s: RecipeStepDoc): string[] {
  const deps = Object.values(s.in ?? {})
    .flatMap(parts)
    .map((p) => splitRef(p)[0])
    .filter((x) => x !== 'inputs')
  return [...new Set([...deps, ...stepRefs(s.params).map(([sid]) => sid)])]
}

// Longest-path depth gives a left-to-right layering that keeps every edge pointing right.
export function depths(ids: string[], deps: (id: string) => string[]): Map<string, number> {
  const memo = new Map<string, number>()
  const known = new Set(ids)
  const visiting = new Set<string>()
  const depth = (id: string): number => {
    const m = memo.get(id)
    if (m !== undefined) return m
    if (visiting.has(id)) return 0
    visiting.add(id)
    const ds = deps(id).filter((d) => known.has(d) && d !== id)
    const v = ds.length ? Math.max(...ds.map(depth)) + 1 : 0
    visiting.delete(id)
    memo.set(id, v)
    return v
  }
  ids.forEach(depth)
  return memo
}

interface Size {
  w: number
  h: number
  laneW: number
  rowH: number
}
const PEEK_SIZE: Size = { w: 208, h: 56, laneW: 244, rowH: 104 }
const RUN_SIZE: Size = { w: 232, h: 100, laneW: 272, rowH: 156 }
const RAIL_JITTER = 4

interface Slot {
  x: number
  y: number
  row: number
  lane: number
}
interface VerticalFlow {
  slots: Map<string, Slot>
  lanes: number
  size: Size
}

function autoPositions(columns: string[][]): Map<string, { x: number; y: number }> {
  const tallest = Math.max(1, ...columns.map((c) => c.length))
  const out = new Map<string, { x: number; y: number }>()
  columns.forEach((col, ci) => {
    const offset = ((tallest - col.length) * ROW_H) / 2
    col.forEach((id, ri) => out.set(id, { x: ci * COL_W, y: offset + ri * ROW_H }))
  })
  return out
}

function nearestFree(base: number, taken: Set<number>): number {
  for (let k = 0; ; k++) for (const l of k ? [base + k, base - k] : [base]) if (l >= 0 && !taken.has(l)) return l
}

// Rows are dependency depths read top to bottom; each parallel branch keeps one lane so a child sits under its parent.
function verticalFlow(rows: string[][], parentsOf: (id: string) => string[], size: Size): VerticalFlow {
  const rowOf = new Map<string, number>()
  rows.forEach((r, i) => r.forEach((id) => rowOf.set(id, i)))
  const lane = new Map<string, number>()
  const slots = new Map<string, Slot>()
  let lanes = 1
  rows.forEach((row, ri) => {
    const pref = new Map<string, number>()
    for (const id of row) {
      const ps = parentsOf(id).filter((x) => lane.has(x))
      const primary = ps.reduce<string | undefined>((a, b) => (a === undefined || rowOf.get(b)! > rowOf.get(a)! ? b : a), undefined)
      pref.set(id, primary === undefined ? 0 : lane.get(primary)!)
    }
    const taken = new Set<number>()
    for (const id of [...row].sort((a, b) => pref.get(a)! - pref.get(b)!)) {
      const l = nearestFree(pref.get(id)!, taken)
      taken.add(l)
      lane.set(id, l)
      lanes = Math.max(lanes, l + 1)
      slots.set(id, { x: l * size.laneW, y: ri * size.rowH, row: ri, lane: l })
    }
  })
  return { slots, lanes, size }
}

// Links run orthogonally through the gaps between rows; one that skips rows drops down a lane no node uses, else takes a side rail.
function routeEdges(pairs: Edge[], flow: VerticalFlow): Edge[] {
  const { slots, size: s } = flow
  const occ = new Set<string>()
  slots.forEach((v) => occ.add(`${v.row}:${v.lane}`))
  const cx = (lane: number): number => lane * s.laneW + s.w / 2
  const gutter = (row: number): number => row * s.rowH + s.h + (s.rowH - s.h) / 2
  const clear = (lane: number, from: number, to: number): boolean => {
    for (let r = from + 1; r < to; r++) if (occ.has(`${r}:${lane}`)) return false
    return true
  }
  const seen = new Map<string, Edge>()
  for (const e of pairs) {
    const key = `${e.source}|${e.target}`
    const prev = seen.get(key)
    if (!prev || (e.class === 'bind' && prev.class !== 'bind')) seen.set(key, e)
  }
  const out: Edge[] = []
  let k = 0
  for (const e of seen.values()) {
    const a = slots.get(e.source)
    const b = slots.get(e.target)
    if (!a || !b) continue
    const n = k++
    const off = ((n % RAIL_JITTER) - (RAIL_JITTER - 1) / 2) * 6
    const skip = b.row > a.row + 1
    let route: Array<[number, number]>
    if (!skip) route = [[cx(a.lane), gutter(a.row) + off], [cx(b.lane), gutter(a.row) + off]]
    else if (clear(a.lane, a.row, b.row)) route = [[cx(a.lane), gutter(b.row - 1) + off], [cx(b.lane), gutter(b.row - 1) + off]]
    else if (clear(b.lane, a.row, b.row)) route = [[cx(a.lane), gutter(a.row) + off], [cx(b.lane), gutter(a.row) + off]]
    else {
      const rail = cx(flow.lanes) - s.w / 2 + 12 + (n % 3) * 6
      route = [[cx(a.lane), gutter(a.row) + off], [rail, gutter(a.row) + off], [rail, gutter(b.row - 1) + off], [cx(b.lane), gutter(b.row - 1) + off]]
    }
    out.push({ ...e, label: undefined, type: 'band', class: skip ? `${e.class ?? ''} jump`.trim() : e.class, deletable: false, data: { route } })
  }
  return out
}

export function kindLabel(kind: string, info?: KindInfo): string {
  return info?.label ?? kind
}

function outPortsOf(s: RecipeStepDoc, info: KindInfo | undefined, used: Set<string>): string[] {
  const declared = info?.out ? Object.keys(info.out).filter((k) => k !== '*') : []
  const passthrough = info?.out && '*' in info.out ? Object.keys(s.in ?? {}) : []
  return [...new Set([...declared, ...passthrough, ...used])]
}

export function buildEditorGraph(
  doc: RecipeDoc,
  kinds: Map<string, KindInfo>,
  issues: RecipeIssue[],
  selected: string | null,
  peek = false
): { nodes: Node[]; edges: Edge[] } {
  const steps = doc.steps ?? []
  const usedOut = new Map<string, Set<string>>()
  const use = (src: string, name: string): void => {
    let s = usedOut.get(src)
    if (!s) usedOut.set(src, (s = new Set()))
    s.add(name)
  }
  for (const s of steps) for (const b of Object.values(s.in ?? {})) for (const p of parts(b)) use(...splitRef(p))
  for (const b of Object.values(doc.outputs ?? {})) use(...splitRef(b))
  for (const s of steps) for (const [sid, out] of stepRefs(s.params)) use(sid, out)

  const ids = steps.map((s) => s.id)
  const byId = new Map(steps.map((s) => [s.id, s]))
  const d = depths(ids, (id) => (byId.get(id) ? stepDeps(byId.get(id)!) : []))
  const maxDepth = Math.max(0, ...d.values())
  const columns: string[][] = [[INPUTS_NODE]]
  for (const id of ids) {
    const c = (d.get(id) ?? 0) + 1
    ;(columns[c] ??= []).push(id)
  }
  columns[maxDepth + 2] = [OUTPUTS_NODE]
  for (let i = 0; i < columns.length; i++) columns[i] ??= []
  const parentMap = new Map<string, string[]>()
  const link = (child: string, src: string): void => {
    if (src !== INPUTS_NODE && !byId.has(src)) return
    parentMap.set(child, [...(parentMap.get(child) ?? []), src])
  }
  for (const s of steps) {
    for (const b of Object.values(s.in ?? {})) for (const p of parts(b)) link(s.id, splitRef(p)[0] === 'inputs' ? INPUTS_NODE : splitRef(p)[0])
    for (const [sid] of stepRefs(s.params)) link(s.id, sid)
  }
  for (const b of Object.values(doc.outputs ?? {})) link(OUTPUTS_NODE, splitRef(b)[0])
  const flow = peek ? verticalFlow(columns, (id) => parentMap.get(id) ?? [], PEEK_SIZE) : null
  const auto = autoPositions(columns)
  const saved = peek ? {} : (doc.ui?.positions ?? {})
  const pos = (id: string): { x: number; y: number } => {
    const p = saved[id]
    const v = flow?.slots.get(id)
    if (v) return { x: v.x, y: v.y }
    return p ? { x: p[0], y: p[1] } : (auto.get(id) ?? { x: 0, y: 0 })
  }

  const inputPorts = Object.entries(doc.inputs ?? {}).map(([name, spec]) => ({
    name,
    type: String(spec.type ?? 'text'),
    bindable: spec.type === 'image' || spec.type === 'mesh' || spec.type === 'file' || spec.type === 'video'
  }))
  const nodes: Node[] = [
    {
      id: INPUTS_NODE,
      type: 'io',
      position: pos(INPUTS_NODE),
      deletable: false,
      data: { role: 'inputs', peek, ports: inputPorts, selected: selected === INPUTS_NODE, issues: [] } satisfies IoNodeData
    }
  ]
  for (const [si, s] of steps.entries()) {
    const info = kinds.get(s.kind)
    const declaredIn = info?.in ? Object.keys(info.in).filter((k) => k !== '*') : []
    const acceptsAny = !info || !info.in || '*' in info.in
    nodes.push({
      id: s.id,
      type: 'step',
      position: pos(s.id),
      data: {
        mode: peek ? 'peek' : 'edit',
        stepId: s.id,
        kind: s.kind,
        lane: info?.lane ?? 'cpu',
        label: kindLabel(s.kind, info),
        inPorts: [...new Set([...declaredIn, ...Object.keys(s.in ?? {})])],
        outPorts: outPortsOf(s, info, usedOut.get(s.id) ?? new Set()),
        acceptsAny,
        mapped: s.map ?? s.map_param ?? null,
        issues: issues.filter((i) => i.step === s.id),
        selected: selected === s.id,
        order: si + 1
      } satisfies StepNodeData
    })
  }
  nodes.push({
    id: OUTPUTS_NODE,
    type: 'io',
    position: pos(OUTPUTS_NODE),
    deletable: false,
    data: {
      role: 'outputs',
      peek,
      ports: Object.keys(doc.outputs ?? {}).map((name) => ({ name, type: 'output', bindable: true })),
      selected: selected === OUTPUTS_NODE,
      issues: issues.filter((i) => i.field?.startsWith('outputs.'))
    } satisfies IoNodeData
  })

  const edges: Edge[] = []
  for (const s of steps) {
    for (const [name, b] of Object.entries(s.in ?? {})) {
      parts(b).forEach((p, i) => {
        const [src, out] = splitRef(p)
        const source = src === 'inputs' ? INPUTS_NODE : src
        if (src !== 'inputs' && !byId.has(src)) return
        edges.push({
          id: `b|${s.id}|${name}|${i}`,
          source,
          target: s.id,
          sourceHandle: `o:${out}`,
          targetHandle: `i:${name}`,
          type: 'smoothstep',
          class: 'bind',
          label: out === name ? undefined : `${out} → ${name}`
        })
      })
    }
    for (const [sid, out] of new Map(stepRefs(s.params).map(([a, b]) => [`${a}.${b}`, [a, b] as const])).values()) {
      if (!byId.has(sid)) continue
      edges.push({
        id: `r|${s.id}|${sid}|${out}`,
        source: sid,
        target: s.id,
        sourceHandle: `o:${out}`,
        targetHandle: 'ref',
        type: 'smoothstep',
        class: 'ref',
        deletable: false,
        label: 'tham chiếu'
      })
    }
  }
  for (const [name, b] of Object.entries(doc.outputs ?? {})) {
    const [src, out] = splitRef(b)
    if (!byId.has(src)) continue
    edges.push({ id: `o|${name}`, source: src, target: OUTPUTS_NODE, sourceHandle: `o:${out}`, targetHandle: `i:${name}`, type: 'smoothstep', class: 'out' })
  }
  if (flow) return { nodes, edges: routeEdges(edges, flow) }
  crowdLabels(edges)
  return { nodes, edges }
}

// Labels of edges that leave one step or enter one step sit on top of each other; those show only while the edge is hovered.
function crowdLabels(edges: Edge[]): void {
  const labelled = edges.filter((e) => e.label)
  const count = (key: (e: Edge) => string): Map<string, number> => {
    const m = new Map<string, number>()
    for (const e of labelled) m.set(key(e), (m.get(key(e)) ?? 0) + 1)
    return m
  }
  const bySource = count((e) => e.source)
  const byTarget = count((e) => e.target)
  for (const e of labelled) {
    if ((bySource.get(e.source) ?? 0) > 1 || (byTarget.get(e.target) ?? 0) > 1) e.class = `${e.class ?? ''} crowded`.trim()
  }
}

export function buildRunGraph(steps: StepRow[], kinds: Map<string, KindInfo>, selected: string | null): { nodes: Node[]; edges: Edge[] } {
  const byKey = new Map(steps.map((s) => [s.key, s]))
  const d = depths(
    steps.map((s) => s.key),
    (k) => byKey.get(k)?.deps ?? []
  )
  const columns: string[][] = []
  for (const s of steps) (columns[d.get(s.key) ?? 0] ??= []).push(s.key)
  for (let i = 0; i < columns.length; i++) columns[i] ??= []
  const flow = verticalFlow(columns, (id) => (byKey.get(id)?.deps ?? []).filter((x) => byKey.has(x)), RUN_SIZE)
  const nodes: Node[] = steps.map((s, si) => ({
    id: s.key,
    type: 'step',
    position: flow.slots.get(s.key) ?? { x: 0, y: 0 },
    draggable: false,
    connectable: false,
    data: {
      mode: 'run',
      vertical: true,
      stepId: s.key,
      kind: s.kind,
      lane: s.lane,
      label: kindLabel(s.kind, kinds.get(s.kind)),
      inPorts: ['in'],
      outPorts: ['out'],
      acceptsAny: false,
      mapped: null,
      issues: [],
      selected: selected === s.id,
      order: si + 1,
      step: s
    } satisfies StepNodeData
  }))
  const edges: Edge[] = []
  for (const s of steps) {
    for (const dep of s.deps) {
      const src = byKey.get(dep)
      if (!src) continue
      const flowing = s.status === 'running' || s.status === 'waiting_resource'
      edges.push({
        id: `${dep}->${s.key}`,
        source: dep,
        target: s.key,
        sourceHandle: 'o:out',
        targetHandle: 'i:in',
        type: 'smoothstep',
        animated: flowing,
        class: src.status === 'succeeded' || src.status === 'cached' || src.status === 'skipped' ? 'done' : flowing ? 'flow' : ''
      })
    }
  }
  return { nodes, edges: routeEdges(edges, flow) }
}

export function uniqueStepId(doc: RecipeDoc, kind: string): string {
  const base = (kind.split('.').pop() ?? 'step').replace(/[^\w-]/g, '_').replace(/^(\d)/, '_$1').slice(0, 24) || 'step'
  const taken = new Set((doc.steps ?? []).map((s) => s.id))
  if (!taken.has(base)) return base
  for (let i = 2; ; i++) if (!taken.has(`${base}${i}`)) return `${base}${i}`
}

function renameInValue(v: unknown, from: string, to: string): unknown {
  if (typeof v === 'string') return v.replaceAll(`\${steps.${from}.`, `\${steps.${to}.`)
  if (Array.isArray(v)) return v.map((x) => renameInValue(x, from, to))
  if (v && typeof v === 'object') return Object.fromEntries(Object.entries(v).map(([k, x]) => [k, renameInValue(x, from, to)]))
  return v
}

export function renameStep(doc: RecipeDoc, from: string, to: string): void {
  const swap = (p: string): string => {
    const [src, out] = splitRef(p)
    return src === from ? `${to}.${out}` : p
  }
  for (const s of doc.steps) {
    if (s.id === from) s.id = to
    if (s.in) for (const [k, b] of Object.entries(s.in)) s.in[k] = Array.isArray(b) ? b.map(swap) : swap(b)
    if (s.params) s.params = renameInValue(s.params, from, to) as Record<string, unknown>
  }
  if (doc.outputs) for (const [k, b] of Object.entries(doc.outputs)) doc.outputs[k] = swap(b)
  const pos = doc.ui?.positions
  if (pos?.[from]) {
    pos[to] = pos[from]!
    delete pos[from]
  }
}

export function removeStep(doc: RecipeDoc, id: string): void {
  doc.steps = doc.steps.filter((s) => s.id !== id)
  for (const s of doc.steps) {
    if (!s.in) continue
    for (const [k, b] of Object.entries(s.in)) {
      const kept = parts(b).filter((p) => splitRef(p)[0] !== id)
      if (!kept.length) {
        delete s.in[k]
        if (s.map === k) s.map = null
      } else s.in[k] = kept.length === 1 ? kept[0]! : kept
    }
  }
  if (doc.outputs) for (const [k, b] of Object.entries(doc.outputs)) if (splitRef(b)[0] === id) delete doc.outputs[k]
  delete doc.ui?.positions?.[id]
}
