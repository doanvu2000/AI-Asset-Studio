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

// op.sheet always names its meta "sheet.json"; the sheet image is "sheet.png" or "<asset>_sheet.png".
const isSheetMeta = (b: BlobRef): boolean => isJson(b) && stem(b) === 'sheet'
const isSheetImage = (b: BlobRef): boolean => isImage(b) && /(^|_)sheet$/.test(stem(b))

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

function spritePair(blob: BlobRef, all: BlobRef[]): { sheet: BlobRef; meta: BlobRef } | null {
  const meta = isSheetMeta(blob) ? blob : all.find(isSheetMeta)
  const sheet = isSheetImage(blob) ? blob : all.find(isSheetImage)
  return meta && sheet ? { sheet, meta } : null
}

/** Views that make sense for `blob`, best first; `siblings` are blobs produced alongside it (same step or run). */
export function viewsFor(blob: BlobRef, siblings: BlobRef[] = []): AssetView[] {
  const all = uniq([blob, ...siblings])
  const out: AssetView[] = []
  if (isGlb(blob)) out.push({ kind: 'glb', blob })

  const spine = spineSet(all)
  const inSpine = !!spine && [spine.skeleton, spine.atlas, spine.texture].some((b) => b.hash === blob.hash)
  const pair = spritePair(blob, all)
  const inSprite = !!pair && (pair.sheet.hash === blob.hash || pair.meta.hash === blob.hash)

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
  if (sheet && meta) return { kind: 'sprite', blob: sheet, sheet, meta }
  return null
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
