import type { KindInfo, RecipeDetail, RecipeInfo } from '@/api/types'
import { api } from '@/stores/connection'
import { splitRef } from './graph'

const HASH_RE = /^[0-9a-f]{64}$/
const DEMO_TTL_MS = 60_000
const PORT_LABEL: Record<string, string> = {
  image: 'ảnh',
  images: 'ảnh',
  mesh: 'model 3D',
  video: 'video',
  file: 'tệp',
  json: 'JSON',
  text: 'văn bản'
}

const details = new Map<string, Promise<RecipeDetail>>()
const demos = new Map<string, { at: number; demo: Promise<RecipeDemo | null> }>()

export function recipeDetail(id: string): Promise<RecipeDetail> {
  let p = details.get(id)
  if (!p) {
    p = api().getRecipe(id)
    details.set(id, p)
    p.catch(() => details.get(id) === p && details.delete(id))
  }
  return p
}

export function forgetRecipeDetail(id: string): void {
  details.delete(id)
}

export type RecipeDemo = { type: 'file'; src: string } | { type: 'blob'; hash: string }

async function usableBlob(hash: string): Promise<boolean> {
  try {
    await api().blobInfo(hash)
    return true
  } catch {
    return false
  }
}

async function latestRunDemo(id: string): Promise<RecipeDemo | null> {
  const runs = await api().listRuns({ recipe_id: id, status: 'succeeded', limit: 5 })
  for (const r of runs) if (r.preview_hash && (await usableBlob(r.preview_hash))) return { type: 'blob', hash: r.preview_hash }
  return null
}

async function loadDemo(recipe: RecipeInfo): Promise<RecipeDemo | null> {
  const own = recipe.preview
  if (own === 'file') return { type: 'file', src: api().recipePreviewUrl(recipe.id) }
  if (own && HASH_RE.test(own) && (await usableBlob(own))) return { type: 'blob', hash: own }
  return latestRunDemo(recipe.id)
}

/** The recipe's shipped demo image, else its newest successful run's image; null when neither is usable. */
export function recipeDemo(recipe: RecipeInfo): Promise<RecipeDemo | null> {
  const hit = demos.get(recipe.id)
  if (hit && Date.now() - hit.at < DEMO_TTL_MS) return hit.demo
  const demo = loadDemo(recipe)
  demos.set(recipe.id, { at: Date.now(), demo })
  demo.catch(() => demos.get(recipe.id)?.demo === demo && demos.delete(recipe.id))
  return demo
}

export function portLabel(type: string | undefined): string {
  if (!type) return ''
  return PORT_LABEL[type.replace('[]', '')] ?? type
}

export function requiredInputs(r: RecipeInfo): { key: string; label: string; type: string }[] {
  return Object.entries(r.inputs ?? {})
    .filter(([, s]) => s.required !== false && s.default === undefined)
    .map(([key, s]) => {
      const label = s.label?.split(/[(,:]/)[0]?.trim() || key
      const type = portLabel(s.type)
      return { key, label, type: label.toLowerCase().includes(type) ? '' : type }
    })
}

/** Output name + type, following a pass-through step (`*`) one hop back to what it receives. */
export function recipeOutputs(detail: RecipeDetail, kinds: Map<string, KindInfo>): { name: string; type: string }[] {
  const steps = new Map(detail.doc.steps.map((s) => [s.id, s]))
  const typeOf = (ref: string): string => {
    const [sid, port] = splitRef(ref)
    const step = steps.get(sid)
    const out = step ? kinds.get(step.kind)?.out : null
    if (!step || !out) return ''
    if (port in out && out[port] !== '*') return out[port] ?? ''
    const bound = step.in?.[port]
    const src = Array.isArray(bound) ? bound[0] : bound
    return src && !src.startsWith('inputs.') ? typeOf(src) : ''
  }
  return Object.entries(detail.doc.outputs ?? {}).map(([name, ref]) => ({ name, type: portLabel(typeOf(ref)) }))
}

const EXPORT_LABEL: Record<string, string> = {
  unity: 'Unity',
  godot: 'Godot',
  spine: 'Spine',
  glb: 'GLB',
  fbx: 'FBX',
  audio: 'Âm thanh'
}

export function exportLabels(r: RecipeInfo | null | undefined): string[] {
  return (r?.exports ?? []).map((e) => EXPORT_LABEL[e] ?? e)
}
