// Sheet angle convention (core op.sheet / blender render_dirs): 0° faces the camera, angles grow toward screen-left.
const NAMES: Record<number, string> = {
  0: 'Xuống',
  45: 'Xuống‑trái',
  90: 'Trái',
  135: 'Lên‑trái',
  180: 'Lên',
  225: 'Lên‑phải',
  270: 'Phải',
  315: 'Xuống‑phải'
}

export function normAngle(v: unknown): number | null {
  const n = typeof v === 'number' ? v : typeof v === 'string' && v.trim() ? Number(v) : NaN
  return Number.isFinite(n) ? ((n % 360) + 360) % 360 : null
}

/** Vietnamese direction name for an angle; off-grid angles within 10° of a compass point borrow its name. */
export function directionName(angle: number): string {
  const snap = (Math.round(angle / 45) * 45) % 360
  const off = Math.abs(((angle - snap + 540) % 360) - 180)
  return off <= 10 ? NAMES[snap]! : `${Math.round(angle)}°`
}

export function angleText(angle: number): string {
  return `${Math.round(angle * 10) / 10}°`.replace('.', ',')
}

/** CSS rotation for a down-pointing arrow glyph so it points the way the sprite faces on screen. */
export function arrowRotation(angle: number): string {
  return `rotate(${angle}deg)`
}
