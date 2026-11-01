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

const ISSUE_TEXT: Array<[RegExp, (m: RegExpExecArray) => string]> = [
  [/^required input '(.+)' of (\S+) is neither set nor mapped$/, (m) => `Ô bắt buộc "${m[1]}" của quy trình ${m[2]} chưa có giá trị và chưa được nối`],
  [/^unknown recipe '(.+)'$/, (m) => `Quy trình "${m[1]}" không còn tồn tại`],
  [/^nodes form a cycle(?:: (.+))?$/, (m) => `Các node nối thành vòng lặp${m[1] ? `: ${m[1]}` : ''}`],
  [/^a workflow needs at least one node$/, () => 'Workflow cần ít nhất một node'],
  [/^title must not be empty$/, () => 'Cần đặt tên cho workflow'],
  [/^invalid workflow id '(.*)'$/, (m) => `Mã workflow "${m[1]}" không hợp lệ (chữ không dấu, số, gạch ngang, bắt đầu bằng chữ)`],
  [/^duplicate node id '(.+)'$/, (m) => `Mã node "${m[1]}" bị trùng`],
  [/^(.+): a node cannot feed itself$/, () => 'Một node không thể tự nối vào chính nó'],
  [/^(.+): unknown node '(.+)'$/, (m) => `${m[1]}: không có node "${m[2]}"`],
  [/^(.+): workflow has no input '(.+)'$/, (m) => `${m[1]}: workflow chưa khai báo đầu vào "${m[2]}"`],
  [/^at most (\d+) nodes/, (m) => `Tối đa ${m[1]} node trong một workflow`]
]

/** The core reports issues in English; known ones are shown translated, the rest verbatim. */
export function issueText(message: string): string {
  for (const [re, fn] of ISSUE_TEXT) {
    const m = re.exec(message)
    if (m) return fn(m)
  }
  return message
}
