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
import sprite3d from './recipe-sprite-8dir-3d.webp'
import sprite3dBrief from './recipe-sprite-8dir-3d-brief.webp'
import sprite3dMv from './recipe-sprite-8dir-3d-mv.webp'
import spriteGlb from './recipe-sprite-8dir-glb.webp'
import prop3d from './recipe-prop-3d.webp'
import procedural3d from './recipe-procedural-3d.webp'
import spineCharacter from './recipe-spine-character.webp'

export const ART = { appIcon, emptyRuns, emptyGates, emptyGallery, coreDisconnected, gettingStarted } as const

const RECIPE_ART: Record<string, string> = {
  'concept-2d': concept2d,
  'concept-2d-codex': concept2dCodex,
  'concept-2d-brief': concept2dBrief,
  'variants-2d': variants2d,
  'sprite-8dir-2d': sprite2d,
  'sprite-8dir-2d-prompt': sprite2d,
  'sprite-8dir-3d': sprite3d,
  'sprite-8dir': sprite3d,
  'sprite-8dir-3d-brief': sprite3dBrief,
  'sprite-8dir-3d-mv': sprite3dMv,
  'sprite-8dir-glb': spriteGlb,
  'prop-3d': prop3d,
  'procedural-3d': procedural3d,
  'spine-character': spineCharacter
}

export function recipeArt(id: string): string | undefined {
  return RECIPE_ART[id]
}

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
