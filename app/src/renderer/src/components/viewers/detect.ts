import type { BlobRef } from '@/api/types'

export type ViewKind = 'glb' | 'sprite' | 'spine' | 'tile' | 'image' | 'audio' | 'text' | 'file'

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
  /** Tileable textures (op.seamless names `*_seamless*`): variants of one material, shown repeated. */
  tiles?: BlobRef[]
  /** op.tile_check report (`tile_check.json`) with the seam score. */
  report?: BlobRef
  pixel?: BlobRef
  /** Per-variant maps, index-aligned with `tiles` (missing entries fall back to the colour texture). */
  normals?: (BlobRef | undefined)[]
  pixels?: (BlobRef | undefined)[]
  /** `tiles` are a 16-tile corner Wang set (`wang_<nn>_<NW NE SW SE>`), previewed as a random valid arrangement. */
  wang?: boolean
  /** op.audio_post's waveform image `<clip>_wave.png`. */
  wave?: BlobRef
}

export const VIEW_LABEL: Record<ViewKind, string> = {
  glb: '3D',
  sprite: 'Hoạt ảnh',
  spine: 'Spine',
  tile: 'Lát',
  image: 'Ảnh',
  audio: 'Âm thanh',
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
export const isAudio = (b: BlobRef): boolean => b.mime.startsWith('audio/') || ['ogg', 'wav', 'mp3', 'flac', 'opus'].includes(ext(b))
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

// Only the core's normal frame names (`dir_000_n`, `walk_dir_000_f00_n_down_n`): `<asset>_N.png` frames stay color.
export const isNormalBlob = (b: BlobRef): boolean =>
  normalHashes.has(b.hash) || /(^|_)normal(_|$)/.test(stem(b)) || /(^|_)dir_\d{3}(_.*)?_n$/.test(stem(b))

/** Same pixel size, so a normal sheet can stand in for the color one frame by frame. */
export const sameSize = (a: BlobRef, b: BlobRef): boolean =>
  !!a.width && !!a.height && a.width === b.width && a.height === b.height

// op.sheet names its meta "sheet.json" ("sheet_normal.json" for normal maps); the image is "sheet.png" or
// "<asset>_sheet.png" (+ "_normal").
const isSheetMeta = (b: BlobRef): boolean => isJson(b) && stem(b) === 'sheet' && !isNormalBlob(b)
const isSheetImage = (b: BlobRef): boolean => isImage(b) && /(^|_)sheet$/.test(stem(b)) && !isNormalBlob(b)
const isNormalSheet = (b: BlobRef): boolean => isImage(b) && isNormalBlob(b) && (normalHashes.has(b.hash) || /sheet/.test(stem(b)))
const isNormalMeta = (b: BlobRef): boolean => isJson(b) && isNormalBlob(b) && /sheet/.test(stem(b))

export const isTileImage = (b: BlobRef): boolean => isImage(b) && /(^|_)seamless(_|$)/.test(stem(b))
const isTileReport = (b: BlobRef): boolean => isJson(b) && stem(b) === 'tile_check'
// Main textures only: op.tile_resize names the extra sizes `<stem>_<px>`.
const isTileMain = (b: BlobRef): boolean => isTileImage(b) && /(^|_)seamless$/.test(stem(b)) && !isNormalBlob(b)

// op.tile_wang names its tiles `wang_<index>_<corner labels a|b in NW NE SW SE order>`.
const WANG_TILE = /^wang_(\d{2})_([ab]{4})$/
export const isWangTile = (b: BlobRef): boolean => isImage(b) && WANG_TILE.test(stem(b))
/** Corner bits (NW=8, NE=4, SW=2, SE=1, b = 1) of a Wang tile, or -1. */
export function wangIndex(b: BlobRef): number {
  const m = WANG_TILE.exec(stem(b))
  if (!m) return -1
  return [...m[2]!].reduce((acc, c) => acc * 2 + (c === 'b' ? 1 : 0), 0)
}

// op.tile_normal / op.tile_pixel name their maps `<stem>_normal` / `<stem>_pixel`, so each variant finds its own.
const mapOf = (t: BlobRef, all: BlobRef[], suffix: string): BlobRef | undefined =>
  all.find((b) => isImage(b) && stem(b) === `${stem(t)}_${suffix}`)

/** The 16-tile Wang set `blob` belongs to (else the last complete one). A retried step leaves several sets among the
 * siblings; each step lists its tiles in index order, so a set ends where the index stops rising. */
function wangSet(blob: BlobRef | null, all: BlobRef[]): BlobRef[] | null {
  const groups: BlobRef[][] = []
  for (const b of all.filter(isWangTile)) {
    const g = groups[groups.length - 1]
    if (g && wangIndex(b) > wangIndex(g[g.length - 1]!)) g.push(b)
    else groups.push([b])
  }
  const full = groups.filter((g) => g.length === 16)
  return (blob && full.find((g) => g.some((b) => b.hash === blob.hash))) ?? full[full.length - 1] ?? null
}

function wangView(blob: BlobRef, all: BlobRef[]): AssetView {
  const tiles = wangSet(blob, all) ?? [blob]
  const normals = tiles.map((t) => mapOf(t, all, 'normal'))
  const report = all.find(isTileReport)
  return {
    kind: 'tile',
    blob,
    tiles,
    wang: true,
    ...(report ? { report } : {}),
    ...(normals.some((n) => n) ? { normal: normals.find((n) => n), normals } : {})
  }
}

function tileView(blob: BlobRef, all: BlobRef[]): AssetView {
  if (isWangTile(blob) && wangSet(blob, all)) return wangView(blob, all)
  const main = all.filter(isTileMain)
  const tiles = isTileMain(blob) ? [blob, ...main.filter((b) => b.hash !== blob.hash)] : [blob]
  const normals = isTileMain(blob) ? tiles.map((t) => mapOf(t, all, 'normal')) : []
  const pixels = isTileMain(blob) ? tiles.map((t) => mapOf(t, all, 'pixel')) : []
  const normal = normals[0]
  const pixel = pixels[0]
  const report = all.find(isTileReport)
  return {
    kind: 'tile',
    blob,
    tiles,
    ...(report ? { report } : {}),
    ...(normal ? { normal, normals } : {}),
    ...(pixel ? { pixel, pixels } : {})
  }
}

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
  const normal = sheet && all.find((b) => isNormalSheet(b) && sameSize(b, sheet))
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
  if (isTileImage(blob) || isWangTile(blob)) out.push(tileView(blob, all))
  if (isImage(blob)) out.push({ kind: 'image', blob })
  if (isAudio(blob)) {
    const wave = all.find((b) => isImage(b) && stem(b) === `${stem(blob)}_wave`)
    out.push({ kind: 'audio', blob, ...(wave ? { wave } : {}) })
  }
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
  if (sheet && meta) {
    const normal = all.find((b) => isNormalSheet(b) && sameSize(b, sheet))
    return { kind: 'sprite', blob: sheet, sheet, meta, ...(normal ? { normal } : {}) }
  }
  const normal = all.find(isNormalSheet)
  const nmeta = all.find(isNormalMeta)
  if (normal && nmeta) return { kind: 'sprite', blob: normal, sheet: normal, meta: nmeta }
  const tile = wangSet(null, all)?.[0] ?? all.find(isTileMain)
  if (tile && all.some(isTileReport)) return tileView(tile, all)
  return null
}

const FEATURE_RANK: Partial<Record<ViewKind, number>> = { glb: 0, spine: 1, sprite: 2, tile: 4 }
const rank = (f: AssetView): number => (f.kind === 'sprite' && f.sheet && isNormalBlob(f.sheet) ? 3 : FEATURE_RANK[f.kind]!)

/** `featuredAsset` over several steps' outputs: a sheet only pairs with the meta of its own step, while the
 * normal toggle may come from a sibling step (`nsheet` next to `sheet`). */
export function featuredAcross(groups: BlobRef[][]): AssetView | null {
  let best: AssetView | null = null
  for (const g of groups) {
    const f = featuredAsset(g)
    if (f && (!best || rank(f) < rank(best))) best = f
  }
  const color = best?.sheet
  if (best?.kind === 'sprite' && color && !best.normal && rank(best) === 2) {
    const normal = groups.flat().find((b) => isNormalSheet(b) && sameSize(b, color))
    if (normal) best = { ...best, normal }
  }
  return best
}

export type FileBadge = 'glb' | 'spine' | 'json' | 'atlas' | 'html' | 'text' | 'audio' | 'file'

export function fileBadge(b: BlobRef): FileBadge {
  if (isGlb(b)) return 'glb'
  if (isAtlas(b)) return 'atlas'
  if (b.mime === 'text/html' || ext(b) === 'html') return 'html'
  if (isJson(b)) return 'json'
  if (b.mime.startsWith('audio/') || /^(ogg|wav|mp3|flac)$/.test(ext(b))) return 'audio'
  if (isTextual(b)) return 'text'
  return 'file'
}
