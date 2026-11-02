import type { Edge, Node } from '@vue-flow/core'
import type { FlowIssue, RecipeInfo, WorkflowChild, WorkflowDef, WorkflowNodeStatus } from '@/api/types'
import { routeEdges, verticalFlow, type Size } from './graph'
import { flowRows, nodeDeps, nodeIssues, nodeTitle, parseSource, recipeName, WINPUTS_NODE, WORKFLOW_REF } from './flows'

export interface WfPort {
  name: string
  type: string
  mapped: boolean
}

export interface WfNodeData {
  mode: 'run' | 'edit' | 'inputs'
  nodeId: string
  title: string
  recipe: string
  order?: number
  status?: WorkflowNodeStatus
  progress?: number | null
  preview?: string | null
  error?: string | null
  attempts?: number
  selected: boolean
  issues: FlowIssue[]
  ins: WfPort[]
  outs: string[]
  inputs: Array<{ name: string; type: string; label: string }>
}

export const WF_RUN_SIZE: Size = { w: 280, h: 140, laneW: 324, rowH: 212 }
const COL_W = 350
const EDIT_HEAD = 58
const PORT_H = 28
const EDIT_GAP = 64

const blank = (): Pick<WfNodeData, 'ins' | 'outs' | 'inputs' | 'issues'> => ({ ins: [], outs: [], inputs: [], issues: [] })

export function buildFlowRunGraph(
  def: Partial<WorkflowDef>,
  children: WorkflowChild[],
  recipes: (id: string) => RecipeInfo | undefined,
  selected: string | null
): { nodes: Node[]; edges: Edge[]; order: string[] } {
  const defs = def.nodes ?? []
  const byId = new Map(defs.map((n) => [n.id, n]))
  const child = new Map(children.map((c) => [c.node_id, c]))
  const flow = verticalFlow(flowRows(defs), (id) => (byId.get(id) ? nodeDeps(byId.get(id)!).filter((d) => byId.has(d)) : []), WF_RUN_SIZE)
  const order = [...flow.slots.entries()].sort((a, b) => a[1].row - b[1].row || a[1].lane - b[1].lane).map(([id]) => id)
  const nodes: Node[] = defs.map((n) => {
    const c = child.get(n.id)
    return {
      id: n.id,
      type: 'wf',
      position: flow.slots.get(n.id) ?? { x: 0, y: 0 },
      draggable: false,
      connectable: false,
      data: {
        ...blank(),
        mode: 'run',
        nodeId: n.id,
        title: nodeTitle(n),
        recipe: recipeName(recipes, n.recipe_id),
        order: order.indexOf(n.id) + 1,
        status: c?.status ?? 'pending',
        progress: c?.progress ?? null,
        preview: c?.preview ?? null,
        error: c?.error ?? null,
        attempts: c?.attempts ?? 0,
        selected: selected === n.id
      } satisfies WfNodeData
    }
  })
  const edges: Edge[] = []
  for (const n of defs)
    for (const dep of nodeDeps(n))
      if (byId.has(dep)) edges.push({ id: `${dep}->${n.id}`, source: dep, target: n.id, sourceHandle: 'o:out', targetHandle: 'i:in', type: 'smoothstep' })
  return { nodes, edges: routeEdges(edges, flow), order }
}

export interface EditCtx {
  recipes: (id: string) => RecipeInfo | undefined
  outputsOf: (recipeId: string) => string[]
  issues: FlowIssue[]
  selected: string | null
  positions: Record<string, { x: number; y: number }>
}

export function buildFlowEditGraph(def: WorkflowDef, ctx: EditCtx): { nodes: Node[]; edges: Edge[] } {
  const used = new Map<string, Set<string>>()
  for (const n of def.nodes)
    for (const src of Object.values(n.map)) {
      const p = parseSource(src)
      if (p?.kind === 'outputs' && p.ref !== WORKFLOW_REF) used.set(p.ref, (used.get(p.ref) ?? new Set()).add(p.name))
    }
  const ins = (n: WorkflowDef['nodes'][number]): WfPort[] =>
    Object.entries(ctx.recipes(n.recipe_id)?.inputs ?? {}).map(([name, spec]) => ({ name, type: spec.type, mapped: name in n.map }))
  const outs = (n: WorkflowDef['nodes'][number]): string[] => [...new Set([...ctx.outputsOf(n.recipe_id), ...(used.get(n.id) ?? [])])]
  const heightOf = (rows: number): number => EDIT_HEAD + Math.max(1, rows) * PORT_H + 10

  const rows = flowRows(def.nodes)
  const byId = new Map(def.nodes.map((n) => [n.id, n]))
  const inputNames = Object.keys(def.inputs)
  const auto = new Map<string, { x: number; y: number }>()
  const colHeights = rows.map((col) => col.map((id) => heightOf(Math.max(ins(byId.get(id)!).length, outs(byId.get(id)!).length))))
  const inputsH = heightOf(inputNames.length)
  const tallest = Math.max(inputsH, ...colHeights.map((c) => c.reduce((a, b) => a + b + EDIT_GAP, -EDIT_GAP)))
  auto.set(WINPUTS_NODE, { x: 0, y: (tallest - inputsH) / 2 })
  rows.forEach((col, ci) => {
    const total = colHeights[ci]!.reduce((a, b) => a + b + EDIT_GAP, -EDIT_GAP)
    let y = (tallest - total) / 2
    col.forEach((id, ri) => {
      auto.set(id, { x: (ci + 1) * COL_W, y })
      y += colHeights[ci]![ri]! + EDIT_GAP
    })
  })
  const pos = (id: string): { x: number; y: number } => ctx.positions[id] ?? auto.get(id) ?? { x: 0, y: 0 }

  const nodes: Node[] = [
    {
      id: WINPUTS_NODE,
      type: 'wf',
      position: pos(WINPUTS_NODE),
      deletable: false,
      data: {
        ...blank(),
        mode: 'inputs',
        nodeId: WINPUTS_NODE,
        title: 'Đầu vào workflow',
        recipe: '',
        selected: ctx.selected === WINPUTS_NODE,
        inputs: Object.entries(def.inputs).map(([name, s]) => ({ name, type: s.type, label: s.label || name })),
        issues: ctx.issues.filter((i) => i.node === null && i.field?.startsWith('inputs.'))
      } satisfies WfNodeData
    }
  ]
  for (const n of def.nodes)
    nodes.push({
      id: n.id,
      type: 'wf',
      position: pos(n.id),
      data: {
        ...blank(),
        mode: 'edit',
        nodeId: n.id,
        title: nodeTitle(n),
        recipe: recipeName(ctx.recipes, n.recipe_id),
        selected: ctx.selected === n.id,
        issues: nodeIssues(ctx.issues, n.id),
        ins: ins(n),
        outs: outs(n)
      } satisfies WfNodeData
    })

  const edges: Edge[] = []
  for (const n of def.nodes)
    for (const [target, src] of Object.entries(n.map)) {
      const p = parseSource(src)
      if (!p) continue
      const source = p.ref === WORKFLOW_REF ? WINPUTS_NODE : p.ref
      if (source !== WINPUTS_NODE && !byId.has(source)) continue
      const reuse = p.kind === 'inputs' && p.ref !== WORKFLOW_REF
      edges.push({ id: `m|${n.id}|${target}`, source, target: n.id, sourceHandle: reuse ? 'ref' : `o:${p.name}`, targetHandle: `i:${target}`, type: 'smoothstep', class: reuse ? 'ref' : 'bind' })
    }
  return { nodes, edges }
}
