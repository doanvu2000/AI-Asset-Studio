import type { FlowIssue, RecipeInfo, StepStatus, WorkflowDef, WorkflowNodeDef, WorkflowNodeStatus, WorkflowStatus } from '@/api/types'
import { depths } from './graph'

export const WORKFLOW_REF = 'workflow'
export const WINPUTS_NODE = '__winputs'
const BLOB = new Set(['image', 'images', 'mesh', 'file', 'video'])
const SOURCE_TAIL = /^(outputs|inputs)\.([A-Za-z_][\w-]*)(?:\[(-?\d+)\])?$/

export const isBlobType = (t: string): boolean => BLOB.has(t)

export interface FlowSource {
  ref: string
  kind: 'outputs' | 'inputs'
  name: string
  index: number | null
}

export function parseSource(src: string): FlowSource | null {
  const i = src.indexOf('.')
  if (i <= 0) return null
  const m = SOURCE_TAIL.exec(src.slice(i + 1))
  if (!m) return null
  return { ref: src.slice(0, i), kind: m[1] as 'outputs' | 'inputs', name: m[2]!, index: m[3] === undefined ? null : Number(m[3]) }
}

export function formatSource(s: FlowSource): string {
  return `${s.ref}.${s.kind}.${s.name}${s.index === null ? '' : `[${s.index}]`}`
}

export function nodeDeps(n: WorkflowNodeDef): string[] {
  const out = new Set<string>()
  for (const src of Object.values(n.map ?? {})) {
    const p = parseSource(src)
    if (p && p.ref !== WORKFLOW_REF && p.ref !== n.id) out.add(p.ref)
  }
  return [...out]
}

/** Dependency depth per row, top to bottom; nodes with a missing or cyclic dependency still land in some row. */
export function flowRows(nodes: WorkflowNodeDef[]): string[][] {
  const byId = new Map(nodes.map((n) => [n.id, n]))
  const d = depths(
    nodes.map((n) => n.id),
    (id) => (byId.get(id) ? nodeDeps(byId.get(id)!) : [])
  )
  const rows: string[][] = []
  for (const n of nodes) (rows[d.get(n.id) ?? 0] ??= []).push(n.id)
  for (let i = 0; i < rows.length; i++) rows[i] ??= []
  return rows
}

export function wouldCycle(nodes: WorkflowNodeDef[], from: string, to: string): boolean {
  if (from === to) return true
  const byId = new Map(nodes.map((n) => [n.id, n]))
  const seen = new Set<string>()
  const stack = [from]
  while (stack.length) {
    const id = stack.pop()!
    if (id === to) return true
    if (seen.has(id)) continue
    seen.add(id)
    const n = byId.get(id)
    if (n) stack.push(...nodeDeps(n))
  }
  return false
}

export function nodeTitle(n: { id: string; title?: string }): string {
  return n.title?.trim() || n.id
}

export function recipeName(recipes: (id: string) => RecipeInfo | undefined, id: string): string {
  return recipes(id)?.name ?? id
}

export function chainNames(def: WorkflowDef, recipes: (id: string) => RecipeInfo | undefined): string[] {
  const rows = flowRows(def.nodes)
  const byId = new Map(def.nodes.map((n) => [n.id, n]))
  return rows.flat().map((id) => recipeName(recipes, byId.get(id)!.recipe_id))
}

export const WF_LABEL: Record<WorkflowStatus, string> = {
  queued: 'Đang xếp hàng',
  running: 'Đang chạy',
  awaiting_human: 'Chờ bạn duyệt',
  succeeded: 'Xong',
  failed: 'Lỗi',
  cancelled: 'Đã huỷ'
}

export const NODE_LABEL: Record<WorkflowNodeStatus, string> = {
  pending: 'Chờ bước trước',
  running: 'Đang chạy',
  awaiting_human: 'Chờ bạn duyệt',
  succeeded: 'Xong',
  failed: 'Lỗi',
  cancelled: 'Đã huỷ',
  blocked: 'Bị chặn do bước trước lỗi'
}

export const NODE_GLYPH: Record<WorkflowNodeStatus, StepStatus> = {
  pending: 'pending',
  running: 'running',
  awaiting_human: 'awaiting_human',
  succeeded: 'succeeded',
  failed: 'failed',
  cancelled: 'cancelled',
  blocked: 'skipped'
}

export function nodeIssues(issues: FlowIssue[], id: string): FlowIssue[] {
  return issues.filter((i) => i.node === id)
}

export function slugId(text: string): string {
  const s = text
    .normalize('NFD')
    .replace(/[̀-ͯ]/g, '')
    .replace(/đ/gi, 'd')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
  return s.slice(0, 40)
}

const str = (v: unknown): string => String(v ?? '')

const ISSUE_TEXT: Record<string, (p: Record<string, unknown>) => string> = {
  required_unset: (p) => `Ô bắt buộc "${str(p.name)}" của quy trình ${str(p.recipe)} chưa có giá trị và chưa được nối`,
  unknown_recipe: (p) => `Quy trình "${str(p.recipe)}" không còn tồn tại`,
  cycle: (p) => `Các node nối thành vòng lặp${Array.isArray(p.nodes) && p.nodes.length ? `: ${p.nodes.join(', ')}` : ''}`,
  no_nodes: () => 'Workflow cần ít nhất một node',
  title_empty: () => 'Cần đặt tên cho workflow',
  invalid_flow_id: (p) => `Mã workflow "${str(p.id)}" không hợp lệ (chữ không dấu, số, gạch ngang, bắt đầu bằng chữ)`,
  duplicate_node_id: (p) => `Mã node "${str(p.id)}" bị trùng`,
  self_feed: () => 'Một node không thể tự nối vào chính nó',
  unknown_node: (p) => `${str(p.source)}: không có node "${str(p.ref)}"`,
  workflow_no_input: (p) => `${str(p.source)}: workflow chưa khai báo đầu vào "${str(p.name)}"`,
  too_many_nodes: (p) => `Tối đa ${str(p.max)} node trong một workflow`
}

/** Known issue codes are shown in Vietnamese; unknown codes fall back to the core's English message. */
export function issueText(issue: FlowIssue): string {
  return ISSUE_TEXT[issue.code]?.(issue.params ?? {}) ?? issue.message
}
