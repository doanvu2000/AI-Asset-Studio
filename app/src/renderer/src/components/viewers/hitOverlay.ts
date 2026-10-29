/** Hit VFX drawn over the sheet's attack rows: per sheet row the impact frame and point (cell pixels). */
export interface HitOverlay {
  rows: Record<number, { frame: number; x: number; y: number }>
  image: HTMLImageElement
  cellW: number
  cellH: number
  cols: number
  count: number
  fps: number
  /** VFX cell height as a share of the character frame height. */
  size: number
  /** Clip to open on (e.g. "attack"). */
  clip?: string
}
