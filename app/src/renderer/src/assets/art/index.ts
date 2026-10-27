import appIcon from './app-icon.webp'
import emptyRuns from './empty-runs.webp'
import emptyGates from './empty-gates.webp'
import emptyGallery from './empty-gallery.webp'
import coreDisconnected from './core-disconnected.webp'
import gettingStarted from './getting-started.webp'
import concept2d from './recipe-concept-2d.webp'
import concept2dCodex from './recipe-concept-2d-codex.webp'
import concept2dBrief from './recipe-concept-2d-brief.webp'
import variants2d from './recipe-variants-2d.webp'
import sprite2d from './recipe-sprite-8dir-2d.webp'
import sprite2dPrompt from './recipe-sprite-8dir-2d-prompt.webp'
import sprite3d from './recipe-sprite-8dir-3d.webp'
import sprite3dBrief from './recipe-sprite-8dir-3d-brief.webp'
import sprite3dMv from './recipe-sprite-8dir-3d-mv.webp'
import spriteGlb from './recipe-sprite-8dir-glb.webp'
import prop3d from './recipe-prop-3d.webp'
import procedural3d from './recipe-procedural-3d.webp'
import spineCharacter from './recipe-spine-character.webp'
import spineCharacterMv from './recipe-spine-character-mv.webp'
import spriteWalk from './recipe-sprite-8dir-walk.webp'
import textureSeamless from './recipe-texture-seamless.webp'
import tilesetSeamless from './recipe-tileset-seamless.webp'

export const ART = { appIcon, emptyRuns, emptyGates, emptyGallery, coreDisconnected, gettingStarted } as const

const RECIPE_ART: Record<string, string> = {
  'concept-2d': concept2d,
  'concept-2d-codex': concept2dCodex,
  'concept-2d-brief': concept2dBrief,
  'variants-2d': variants2d,
  'sprite-8dir-2d': sprite2d,
  'sprite-8dir-2d-prompt': sprite2dPrompt,
  'sprite-8dir-3d': sprite3d,
  'sprite-8dir': sprite3d,
  'sprite-8dir-3d-brief': sprite3dBrief,
  'sprite-8dir-3d-mv': sprite3dMv,
  'sprite-8dir-glb': spriteGlb,
  'sprite-8dir-walk': spriteWalk,
  'sprite-8dir-anim': spriteWalk,
  'sprite-8dir-pixel': spriteGlb,
  'sprite-8dir-3d-pixel': sprite3d,
  'prop-3d': prop3d,
  'procedural-3d': procedural3d,
  'spine-character': spineCharacter,
  'spine-character-mv': spineCharacterMv,
  'texture-seamless': textureSeamless,
  'tileset-seamless': tilesetSeamless
}

export function recipeArt(id: string): string | undefined {
  return RECIPE_ART[id]
}

// What a card promises, in the user's words; the step list stays in the tooltip-level detail.
const RECIPE_PITCH: Record<string, string> = {
  'concept-2d': 'Prompt → ảnh PNG nền trong suốt',
  'concept-2d-codex': 'Prompt → ảnh PNG nền trong suốt, không cần GPU',
  'concept-2d-brief': 'Ý tưởng ngắn → Claude viết prompt → ảnh PNG nền trong suốt',
  'variants-2d': 'Ảnh có sẵn + lệnh sửa → nhiều biến thể + sprite sheet',
  'sprite-8dir-2d': 'Ảnh mặt trước → sheet 8 hướng nhanh, góc gần đúng (xem thử)',
  'sprite-8dir-2d-prompt': 'Prompt → sheet 8 hướng nhanh, góc gần đúng (xem thử)',
  'sprite-8dir-3d': 'Prompt → sprite sheet 8 hướng đúng góc, dựng qua mesh 3D',
  'sprite-8dir-3d-mv': 'Prompt → sprite sheet 8 hướng, mặt sau chuẩn nhất',
  'sprite-8dir-3d-brief': 'Ý tưởng ngắn → Claude viết brief → sprite sheet 8 hướng',
  'sprite-8dir-glb': 'Mesh GLB có sẵn → sprite sheet 8 hướng',
  'prop-3d': 'Prompt → vật phẩm 3D (GLB có texture)',
  'procedural-3d': 'Ý tưởng → Claude dựng 3D low-poly bằng code → GLB',
  'sprite-8dir-walk': 'Prompt → sprite 8 hướng + sheet đi bộ 8 hướng, dựng qua mesh 3D',
  'sprite-8dir-anim': 'Prompt → sprite 8 hướng + bộ anim đứng / đi / chạy / chém, kèm normal map',
  'sprite-8dir-pixel': 'Mesh GLB có sẵn → sprite sheet 8 hướng pixel art, một bảng màu chung',
  'sprite-8dir-3d-pixel': 'Prompt → sprite sheet 8 hướng pixel art, dựng qua mesh 3D',
  'spine-character': 'Prompt → nhân vật Spine 4.2 có xương + 6 anim',
  'spine-character-mv': 'Prompt → 2 bộ Spine mặt trước + mặt sau, cùng 6 anim',
  'texture-seamless': 'Prompt vật liệu → texture lặp liền mạch + điểm mối nối, normal map, bản nhỏ',
  'tileset-seamless': 'Prompt vật liệu → nhiều biến thể chung mép, lát trộn lẫn không lộ nối'
}

export function recipePitch(id: string): string | undefined {
  return RECIPE_PITCH[id]
}

export const RECOMMENDED_RECIPES: ReadonlySet<string> = new Set(['sprite-8dir-3d-mv'])

export type RecipeGroup = '2d' | '3d' | 'character'

export const RECIPE_GROUP_LABEL: Record<RecipeGroup, string> = {
  '2d': '2D',
  '3d': '3D',
  character: 'Nhân vật'
}

// Sprites and rigs are characters to the user regardless of whether they pass through a 3D mesh.
export function recipeGroup(id: string): RecipeGroup {
  if (id.startsWith('sprite-') || id.startsWith('spine-') || id.includes('character')) return 'character'
  if (id.includes('3d') || id.includes('glb') || id.includes('mesh')) return '3d'
  return '2d'
}
