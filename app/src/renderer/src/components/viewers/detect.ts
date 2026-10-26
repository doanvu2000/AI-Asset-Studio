import type { BlobRef } from '@/api/types'

export type ViewKind = 'glb' | 'sprite' | 'spine' | 'image' | 'text' | 'file'

export interface SpineSet {
  skeleton: BlobRef
  atlas: BlobRef
  texture: BlobRef
}

export interface AssetView {
  kind: ViewKind
  blob: BlobRef
  sheet?: BlobRef
  meta?: BlobRef
  /** Normal-map sheet with the same layout as `sheet` (meta contract: output `normal_sheet`). */
  normal?: BlobRef
  spine?: SpineSet
}

export const VIEW_LABEL: Record<ViewKind, string> = {
  glb: '3D',
  sprite: 'Hoạt ảnh',
  spine: 'Spine',
  image: 'Ảnh',
  text: 'Nội dung',
  file: 'Tệp'
}

const ext = (b: BlobRef): string => {
  const n = (b.name ?? '').toLowerCase()
  const i = n.lastIndexOf('.')
  return i >= 0 ? n.slice(i + 1) : ''
}
const stem = (b: BlobRef): string => (b.name ?? '').replace(/\.[^.]+$/, '').toLowerCase()

export const isGlb = (b: BlobRef): boolean => b.mime === 'model/gltf-binary' || ext(b) === 'glb'
export const isImage = (b: BlobRef): boolean => b.mime.startsWith('image/')
export const isJson = (b: BlobRef): boolean => b.mime === 'application/json' || ext(b) === 'json'
export const isAtlas = (b: BlobRef): boolean => ext(b) === 'atlas'
const isTextual = (b: BlobRef): boolean =>
  isJson(b) || isAtlas(b) || b.mime.startsWith('text/') || ['txt', 'yaml', 'yml', 'md', 'py', 'log'].includes(ext(b))

// Output ports only exist on step rows; blobs listed from them remember which ones are normal maps.
const normalHashes = new Set<string>()
const NORMAL_PORT = /^normal(_|$)/

/** Flattens a step's outputs, noting blobs on `normal_*` ports so name-agnostic detection still works. */
export function outputBlobs(outputs: Record<string, BlobRef[]>): BlobRef[] {
  for (const [port, list] of Object.entries(outputs)) if (NORMAL_PORT.test(port)) for (const b of list) normalHashes.add(b.hash)
  return Object.values(outputs).flat()
}

// No `_n` suffix rule: direction frames are named `<asset>_N.png` and would read as normal maps.
export const isNormalBlob = (b: BlobRef): boolean => normalHashes.has(b.hash) || /(^|_)normal(_|$)/.test(stem(b))

// op.sheet names its meta "sheet.json" ("sheet_normal.json" for normal maps); the image is "sheet.png" or
// "<asset>_sheet.png" (+ "_normal").
const isSheetMeta = (b: BlobRef): boolean => isJson(b) && stem(b) === 'sheet' && !isNormalBlob(b)
const isSheetImage = (b: BlobRef): boolean => isImage(b) && /(^|_)sheet$/.test(stem(b)) && !isNormalBlob(b)
const isNormalSheet = (b: BlobRef): boolean => isImage(b) && isNormalBlob(b) && (normalHashes.has(b.hash) || /sheet/.test(stem(b)))
const isNormalMeta = (b: BlobRef): boolean => isJson(b) && isNormalBlob(b) && /sheet/.test(stem(b))

function uniq(list: BlobRef[]): BlobRef[] {
  const seen = new Set<string>()
  return list.filter((b) => !seen.has(b.hash) && !!seen.add(b.hash))
}

export function spineSet(blobs: BlobRef[]): SpineSet | null {
  const atlas = blobs.find(isAtlas)
  if (!atlas) return null
  const s = stem(atlas)
  const jsons = blobs.filter((b) => isJson(b) && !isSheetMeta(b))
  const skeleton = jsons.find((b) => stem(b) === s) ?? jsons.find((b) => !/report|meta|layers|parts/.test(stem(b)))
  const pngs = blobs.filter((b) => isImage(b) && b.mime === 'image/png')
  const texture = pngs.find((b) => stem(b) === s) ?? pngs.find((b) => !/preview|frames|setup/.test(stem(b)))
  return skeleton && texture ? { skeleton, atlas, texture } : null
}

function spritePair(blob: BlobRef, all: BlobRef[]): { sheet: BlobRef; meta: BlobRef; normal?: BlobRef } | null {
  // A normal sheet opened on its own plays with its own meta; it has the same layout as the color sheet.
  if (isNormalSheet(blob) || isNormalMeta(blob)) {
    const sheet = isNormalSheet(blob) ? blob : all.find(isNormalSheet)
    const meta = isNormalMeta(blob) ? blob : all.find(isNormalMeta)
    if (sheet && meta) return { sheet, meta }
  }
  const meta = isSheetMeta(blob) ? blob : all.find(isSheetMeta)
  const sheet = isSheetImage(blob) ? blob : all.find(isSheetImage)
  const normal = all.find(isNormalSheet)
  return meta && sheet ? { sheet, meta, ...(normal ? { normal } : {}) } : null
}

/** Views that make sense for `blob`, best first; `siblings` are blobs produced alongside it (same step or run). */
export function viewsFor(blob: BlobRef, siblings: BlobRef[] = []): AssetView[] {
  const all = uniq([blob, ...siblings])
  const out: AssetView[] = []
  if (isGlb(blob)) out.push({ kind: 'glb', blob })

  const spine = spineSet(all)
  const inSpine = !!spine && [spine.skeleton, spine.atlas, spine.texture].some((b) => b.hash === blob.hash)
  const pair = spritePair(blob, all)
  const inSprite = !!pair && (pair.sheet.hash === blob.hash || pair.meta.hash === blob.hash || pair.normal?.hash === blob.hash)

  if (spine && (inSpine || blob.mime === 'text/html')) out.push({ kind: 'spine', blob, spine })
  if (pair && inSprite) out.push({ kind: 'sprite', blob, ...pair })
  if (isImage(blob)) out.push({ kind: 'image', blob })
  if (isTextual(blob)) out.push({ kind: 'text', blob })
  if (!out.length) out.push({ kind: 'file', blob })
  return out
}

/** The one blob worth showing in a large viewer for a set of outputs (GLB, Spine rig, sprite sheet), if any. */
export function featuredAsset(blobs: BlobRef[]): AssetView | null {
  const all = uniq(blobs)
  const glb = all.find(isGlb)
  if (glb) return { kind: 'glb', blob: glb }
  const spine = spineSet(all)
  if (spine) return { kind: 'spine', blob: spine.skeleton, spine }
  const sheet = all.find(isSheetImage)
  const meta = all.find(isSheetMeta)
  const normal = all.find(isNormalSheet)
  if (sheet && meta) return { kind: 'sprite', blob: sheet, sheet, meta, ...(normal ? { normal } : {}) }
  const nmeta = all.find(isNormalMeta)
  if (normal && nmeta) return { kind: 'sprite', blob: normal, sheet: normal, meta: nmeta }
  return null
}

const FEATURE_RANK: Partial<Record<ViewKind, number>> = { glb: 0, spine: 1, sprite: 2 }
const rank = (f: AssetView): number => (f.kind === 'sprite' && f.sheet && isNormalBlob(f.sheet) ? 3 : FEATURE_RANK[f.kind]!)

/** `featuredAsset` over several steps' outputs: a sheet only pairs with the meta of its own step, while the
 * normal toggle may come from a sibling step (`nsheet` next to `sheet`). */
export function featuredAcross(groups: BlobRef[][]): AssetView | null {
  let best: AssetView | null = null
  for (const g of groups) {
    const f = featuredAsset(g)
    if (f && (!best || rank(f) < rank(best))) best = f
  }
  if (best?.kind === 'sprite' && !best.normal && rank(best) === 2) {
    const normal = groups.flat().find(isNormalSheet)
    if (normal) best = { ...best, normal }
  }
  return best
}

export type FileBadge = 'glb' | 'spine' | 'json' | 'atlas' | 'html' | 'text' | 'file'

export function fileBadge(b: BlobRef): FileBadge {
  if (isGlb(b)) return 'glb'
  if (isAtlas(b)) return 'atlas'
  if (b.mime === 'text/html' || ext(b) === 'html') return 'html'
  if (isJson(b)) return 'json'
  if (isTextual(b)) return 'text'
  return 'file'
}
