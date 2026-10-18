import appIcon from './app-icon.webp'
import emptyRuns from './empty-runs.webp'
import emptyGates from './empty-gates.webp'
import emptyGallery from './empty-gallery.webp'
import coreDisconnected from './core-disconnected.webp'
import concept2d from './recipe-concept-2d.webp'
import concept2dCodex from './recipe-concept-2d-codex.webp'
import variants2d from './recipe-variants-2d.webp'
import sprite2d from './recipe-sprite-8dir-2d.webp'
import sprite3d from './recipe-sprite-8dir-3d.webp'
import spriteGlb from './recipe-sprite-8dir-glb.webp'
import prop3d from './recipe-prop-3d.webp'

export const ART = { appIcon, emptyRuns, emptyGates, emptyGallery, coreDisconnected } as const

const RECIPE_ART: Record<string, string> = {
  'concept-2d': concept2d,
  'concept-2d-codex': concept2dCodex,
  'variants-2d': variants2d,
  'sprite-8dir-2d': sprite2d,
  'sprite-8dir-2d-prompt': sprite2d,
  'sprite-8dir-3d': sprite3d,
  'sprite-8dir': sprite3d,
  'sprite-8dir-glb': spriteGlb,
  'prop-3d': prop3d
}

export function recipeArt(id: string): string | undefined {
  return RECIPE_ART[id]
}
