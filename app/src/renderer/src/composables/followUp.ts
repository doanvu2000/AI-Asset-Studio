import type { BlobRef, Outputs, RecipeInfo, RecipeInputSpec } from '@/api/types'

const VIDEO_EXT = ['mp4', 'webm', 'mov', 'mkv']
const BLOB_TYPES = new Set(['image', 'images', 'mesh', 'file', 'video'])
// Spine exports name their atlas pages `texture`; an atlas input wants them right after the atlas file.
const PAGE_KEYS = ['texture', 'textures', 'pages']

export const extOf = (name?: string | null): string => (name?.includes('.') ? name.toLowerCase().split('.').pop()! : '')

export function fits(spec: RecipeInputSpec, b: BlobRef): boolean {
  const ext = extOf(b.name)
  switch (spec.type) {
    case 'image':
    case 'images':
      return b.mime.startsWith('image/')
    case 'mesh':
      return ext === 'glb' || b.mime === 'model/gltf-binary'
    case 'video':
      return b.mime.startsWith('video/') || VIDEO_EXT.includes(ext)
    case 'file':
      return !!spec.ext?.length && spec.ext.includes(ext)
    default:
      return false
  }
}

export interface FollowUp {
  recipe: RecipeInfo
  /** Blob input name → hashes, in the order the input expects them. */
  prefill: Record<string, string[]>
  /** Labels of required file inputs the user still has to add. */
  missing: string[]
}

/**
 * Recipes the clicked output can feed, built from each recipe's declared inputs. Other outputs of the same step
 * fill the remaining file inputs, so a Spine preview offers skin/anim recipes with skeleton + atlas already set.
 */
export function followUps(recipes: RecipeInfo[], clicked: BlobRef, siblings: Outputs = {}): FollowUp[] {
  const pool = uniq([clicked, ...Object.values(siblings).flat()])
  const pages = PAGE_KEYS.map((k) => siblings[k]).find((l) => l?.length) ?? []
  const out: FollowUp[] = []
  for (const recipe of recipes) {
    const blobInputs = Object.entries(recipe.inputs).filter(([, s]) => BLOB_TYPES.has(s.type))
    const required = blobInputs.filter(([, s]) => s.required !== false)
    if (!required.length) continue
    const prefill: Record<string, string[]> = {}
    const used = new Set<string>()
    const target = required.find(([, s]) => fits(s, clicked)) ?? blobInputs.find(([, s]) => fits(s, clicked))
    if (target) {
      prefill[target[0]] = [clicked.hash]
      used.add(clicked.hash)
    }
    // An output named like the input (meta → meta, sockets → sockets) wins before any other file of the same type,
    // so two JSON inputs do not swap their files.
    for (const byName of [true, false])
      for (const [name, spec] of blobInputs) {
        if (prefill[name]) continue
        const hit = (byName ? (siblings[name] ?? []) : pool).find((b) => !used.has(b.hash) && fits(spec, b))
        if (!hit) continue
        used.add(hit.hash)
        prefill[name] = spec.siblings === 'atlas_pages' ? [hit.hash, ...pages.map((p) => p.hash)] : [hit.hash]
      }
    const missing = required.filter(([n]) => !prefill[n]).map(([n, s]) => s.label ?? n)
    // Without the clicked file in a required slot, only multi-file recipes (skeleton + atlas…) that the step's other
    // outputs mostly fill are worth offering; single-file ones would just reuse some unrelated sibling.
    const viaSiblings = required.length - missing.length >= 2
    if (!(target && target[1].required !== false) && !viaSiblings) continue
    out.push({ recipe, prefill, missing })
  }
  return out.sort((a, b) => a.missing.length - b.missing.length || a.recipe.name.localeCompare(b.recipe.name, 'vi'))
}

function uniq(list: BlobRef[]): BlobRef[] {
  const seen = new Set<string>()
  return list.filter((b) => !seen.has(b.hash) && !!seen.add(b.hash))
}

/** `/new` query that opens a recipe with its file inputs already filled (read back by NewRunView). */
export function prefillQuery(recipeId: string, prefill: Record<string, string[]>, projectId?: string): Record<string, string | string[] | undefined> {
  const q: Record<string, string | string[] | undefined> = { recipe: recipeId, project: projectId && projectId !== 'default' ? projectId : undefined }
  for (const [name, hashes] of Object.entries(prefill)) q[`input.${name}`] = hashes.length === 1 ? hashes[0] : hashes
  return q
}

export const PREFILL_PREFIX = 'input.'
