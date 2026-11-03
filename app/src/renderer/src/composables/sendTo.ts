import { onScopeDispose, watch } from 'vue'
import { useRoute, useRouter, type LocationQueryRaw } from 'vue-router'
import type { BlobRef, ProjectRow, RecipeInfo } from '@/api/types'
import { NO_PROJECT } from '@/api/types'
import { followUps, prefillQuery } from './followUp'

export type SendTarget = 'recipe' | 'restyle' | 'audit' | 'pack' | 'gallery' | 'import'

/** What the caller has in hand; each destination only shows when the fields it needs are present. */
export interface SendSource {
  projectId?: string
  /** First image/file feeds recipe suggestions and Restyle's style reference. */
  blobs?: BlobRef[]
  runId?: string
  versionIds?: string[]
  /** Steps whose assets Pack preselects. */
  stepIds?: string[]
  /** Project folder on disk (Audit/Restyle) and files inside it, relative. */
  folder?: string
  files?: string[]
  /** Absolute paths of files outside the Library that "Nhập vào thư viện" copies in. */
  importPaths?: string[]
  /** Hide destinations that would point back at the screen the menu sits on. */
  omit?: SendTarget[]
}

export interface SendItem {
  id: string
  target: SendTarget
  label: string
  hint?: string
  to?: { path: string; query: LocationQueryRaw }
}

const MAX_RECIPES = 4
const IMAGE_EXT = /\.(png|jpe?g|webp)$/i
/** Joins scan-relative paths onto the folder using the folder's own separator. */
export function absolutePaths(root: string, rels: string[]): string[] {
  const sep = root.includes('\\') ? '\\' : '/'
  const base = root.replace(/[\\/]+$/, '')
  return rels.filter((r) => IMAGE_EXT.test(r)).map((r) => `${base}${sep}${r.replace(/[\\/]+/g, sep)}`)
}
const globOf = (rel: string): string => rel.replace(/[,*?[\]]/g, '?')

export function sendItems(src: SendSource, recipes: RecipeInfo[], project?: ProjectRow): SendItem[] {
  const out: SendItem[] = []
  const pid = src.projectId && src.projectId !== NO_PROJECT ? src.projectId : undefined
  const skip = new Set(src.omit)
  const first = src.blobs?.[0]
  if (first && !skip.has('recipe'))
    for (const f of followUps(recipes, first, {}).filter((x) => !x.missing.length).slice(0, MAX_RECIPES))
      out.push({ id: `recipe-${f.recipe.id}`, target: 'recipe', label: f.recipe.name, hint: 'Tạo mới', to: { path: '/new', query: prefillQuery(f.recipe.id, f.prefill, pid) } })

  const folder = src.folder ?? (project?.auto_export_dir || undefined)
  const rels = (src.files ?? []).filter((r) => IMAGE_EXT.test(r))
  if (!skip.has('restyle')) {
    const query: LocationQueryRaw = { project: pid }
    if (src.folder) {
      query.folder = src.folder
      if (rels.length) query.include = rels.map(globOf).join(',')
      query.scan = '1'
      out.push({ id: 'restyle', target: 'restyle', label: 'Restyle', hint: rels.length ? `${rels.length} ảnh đã chọn` : 'cả thư mục', to: { path: '/restyle', query } })
    } else if (first?.mime.startsWith('image/')) {
      query.ref = first.hash
      out.push({ id: 'restyle', target: 'restyle', label: 'Restyle', hint: 'làm ảnh style tham chiếu', to: { path: '/restyle', query } })
    } else if (pid) out.push({ id: 'restyle', target: 'restyle', label: 'Restyle', hint: 'dùng style dự án', to: { path: '/restyle', query } })
  }
  if (folder && !skip.has('audit'))
    out.push({ id: 'audit', target: 'audit', label: 'Kiểm tra project', hint: src.folder ? undefined : 'thư mục xuất của dự án', to: { path: '/audit', query: { folder, scan: '1' } } })
  if (src.stepIds?.length && pid && !skip.has('pack')) out.push({ id: 'pack', target: 'pack', label: 'Đóng gói asset pack', hint: `${src.stepIds.length} asset` })
  if (src.importPaths?.length && !skip.has('import'))
    out.push({ id: 'import', target: 'import', label: 'Nhập vào thư viện', hint: `${src.importPaths.length} file` })
  if ((pid || src.runId || src.versionIds?.length) && !skip.has('gallery')) {
    const query: LocationQueryRaw = { project: pid, run: src.runId, focus: src.versionIds?.[0] }
    out.push({ id: 'gallery', target: 'gallery', label: 'Thư viện', hint: src.versionIds?.length ? 'làm nổi bản này' : src.runId ? 'bản của lượt chạy' : 'lọc theo dự án', to: { path: '/gallery', query } })
  }
  return out
}

/**
 * Reads one-shot query keys (a menu elsewhere sent us here), hands them over and strips them from the URL so Back and
 * reload do not replay them. Also fires when the view is already mounted and only the query changes.
 */
export function useQueryPrefill(keys: string[], apply: (q: Record<string, string>) => void): void {
  const route = useRoute()
  const router = useRouter()
  const stop = watch(
    () => keys.map((k) => route.query[k]).join('\u0000'),
    () => {
      const got: Record<string, string> = {}
      const rest = { ...route.query }
      for (const k of keys) {
        const v = route.query[k]
        const s = Array.isArray(v) ? v[0] : v
        if (typeof s === 'string' && s) got[k] = s
        delete rest[k]
      }
      if (!Object.keys(got).length) return
      apply(got)
      void router.replace({ query: rest })
    },
    { immediate: true }
  )
  onScopeDispose(stop)
}
