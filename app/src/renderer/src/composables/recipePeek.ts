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
let demos: { at: number; byRecipe: Map<string, string> } | null = null
let demoLoad: Promise<Map<string, string>> | null = null

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

/** Latest successful run's preview blob per recipe; one list call serves every card. */
async function demoMap(): Promise<Map<string, string>> {
  if (demos && Date.now() - demos.at < DEMO_TTL_MS) return demos.byRecipe
  demoLoad ??= api()
    .listRuns({ status: 'succeeded', limit: 300 })
    .then((runs) => {
      const byRecipe = new Map<string, string>()
      for (const r of runs) if (r.preview_hash && !byRecipe.has(r.recipe_id)) byRecipe.set(r.recipe_id, r.preview_hash)
      demos = { at: Date.now(), byRecipe }
      return byRecipe
    })
    .finally(() => (demoLoad = null))
  return demoLoad
}

/** The recipe's own `preview` blob hash when it declares one, else its latest successful run's image. */
export async function recipeDemo(detail: RecipeDetail | null, id: string): Promise<string | null> {
  const own = detail?.doc.preview
  if (typeof own === 'string' && HASH_RE.test(own)) return own
  return (await demoMap()).get(id) ?? null
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

export function exportEngines(description: string): string[] {
  return ['Unity', 'Godot'].filter((e) => description.includes(e))
}
