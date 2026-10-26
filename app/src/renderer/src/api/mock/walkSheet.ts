// Procedural stand-in for a Blender walk/idle sheet so the viewer's clip, direction and normal-map UI can be checked.
const CELL = 64
const ANGLES = [0, 45, 90, 135, 180, 225, 270, 315]
const CLIPS = [
  { name: 'walk', frames: 6, fps: 10, loop: true },
  { name: 'idle', frames: 4, fps: 6, loop: true },
  { name: 'attack', frames: 5, fps: 12, loop: false }
]

export interface WalkSheet {
  color: Blob
  normal: Blob
  meta: Record<string, unknown>
  normalMeta: Record<string, unknown>
}

function figure(ctx: OffscreenCanvasRenderingContext2D, x: number, y: number, angle: number, clip: string, f: number, n: number, normal: boolean): void {
  const t = (f / n) * Math.PI * 2
  const bob = clip === 'idle' ? Math.sin(t) * 1.5 : clip === 'walk' ? Math.abs(Math.sin(t)) * -2 : 0
  const swing = clip === 'walk' ? Math.sin(t) * 7 : 0
  const lunge = clip === 'attack' ? Math.sin((f / (n - 1)) * Math.PI) * 9 : 0
  const rad = (angle * Math.PI) / 180
  const dx = -Math.sin(rad)
  const dy = Math.cos(rad)
  const cx = x + CELL / 2 + dx * lunge * 0.6
  const cy = y + 34 + bob
  const hue = angle
  const fill = (light: number): string | CanvasGradient => {
    if (!normal) return `hsl(${hue} 55% ${light}%)`
    const g = ctx.createRadialGradient(cx - 4, cy - 8, 2, cx, cy, 22)
    g.addColorStop(0, '#8080ff')
    g.addColorStop(0.6, '#a08cf0')
    g.addColorStop(1, '#6a9ad8')
    return g
  }
  ctx.fillStyle = fill(38)
  for (const side of [-1, 1]) {
    ctx.beginPath()
    ctx.ellipse(cx + side * 6 + dx * swing * side, cy + 18 + dy * swing * side * 0.5, 4, 6, 0, 0, Math.PI * 2)
    ctx.fill()
  }
  ctx.fillStyle = fill(52)
  ctx.beginPath()
  ctx.ellipse(cx, cy, 13, 16, 0, 0, Math.PI * 2)
  ctx.fill()
  ctx.beginPath()
  ctx.arc(cx, cy - 18, 9, 0, Math.PI * 2)
  ctx.fill()
  ctx.fillStyle = normal ? '#c0c0ff' : '#fff'
  ctx.beginPath()
  ctx.arc(cx + dx * 6, cy - 18 + dy * 4, 2.6, 0, Math.PI * 2)
  ctx.fill()
  if (clip === 'attack') {
    ctx.strokeStyle = normal ? '#9090ff' : `hsl(${hue} 70% 30%)`
    ctx.lineWidth = 3
    ctx.beginPath()
    ctx.moveTo(cx + dx * 10, cy + dy * 6)
    ctx.lineTo(cx + dx * (14 + lunge * 1.6), cy + dy * (8 + lunge))
    ctx.stroke()
  }
}

async function paint(normal: boolean, cols: number, rows: number): Promise<Blob> {
  const c = new OffscreenCanvas(cols * CELL, rows * CELL)
  const ctx = c.getContext('2d')!
  let r = 0
  for (const clip of CLIPS) {
    for (const a of ANGLES) {
      for (let f = 0; f < clip.frames; f++) figure(ctx, f * CELL, r * CELL, a, clip.name, f, clip.frames, normal)
      r++
    }
  }
  return c.convertToBlob({ type: 'image/png' })
}

export async function renderWalkSheet(): Promise<WalkSheet> {
  const cols = Math.max(...CLIPS.map((c) => c.frames))
  const rows = CLIPS.length * ANGLES.length
  const clips = CLIPS.map((c, i) => ({ ...c, row0: i * ANGLES.length, rows: ANGLES.length }))
  const meta = {
    frame_w: CELL,
    frame_h: CELL,
    cols,
    rows,
    count: cols * rows,
    padding: 0,
    pivot: [CELL / 2, 58],
    width: cols * CELL,
    height: rows * CELL,
    row_angles: CLIPS.flatMap(() => ANGLES),
    clips
  }
  const [color, normal] = await Promise.all([paint(false, cols, rows), paint(true, cols, rows)])
  return { color, normal, meta, normalMeta: { ...meta, normal: true } }
}
