import type { ProjectStyle, RecipeInputSpec } from '@/api/types'

export function emptyStyle(): ProjectStyle {
  return { prompt_prefix: '', prompt_suffix: '', negative: '', preset: null, pixel: null, palette_image: null, reference_image: null, inputs: {}, notes: '' }
}

export function normalizeStyle(s: Partial<ProjectStyle> | null | undefined): ProjectStyle {
  return { ...emptyStyle(), ...(s ?? {}), inputs: { ...(s?.inputs ?? {}) } }
}

const PROMPT_INPUTS = ['prompt', 'idea']

const FILL_LABEL: Record<string, string> = {
  prompt: 'phong cách',
  idea: 'phong cách',
  style: 'phong cách',
  negative: 'negative prompt',
  preset: 'preset',
  pixel: 'cỡ pixel',
  palette: 'bảng màu',
  colors: 'số màu',
  outline: 'viền',
  dither: 'dither',
  palette_image: 'ảnh bảng màu',
  image: 'ảnh tham chiếu'
}

/** "phong cách, preset, cỡ pixel" for the keys a run's $meta.project_applied lists. */
export function appliedText(keys: unknown, specs: Record<string, RecipeInputSpec> = {}): string {
  if (!Array.isArray(keys)) return ''
  return [...new Set(keys.map((k) => FILL_LABEL[String(k)] ?? specs[String(k)]?.label ?? String(k)))].join(', ')
}

export interface StyleFill {
  key: string
  label: string
  value: unknown
  /** Prompt inputs are wrapped with prefix/suffix, never replaced; the user's text still goes in. */
  wraps?: boolean
}

const empty = (v: unknown): boolean => v === null || v === undefined || v === '' || (Array.isArray(v) && !v.length)

// Rough mirror of coerce_value: a style value the recipe would reject is skipped by the core, so it is not promised here.
function accepts(spec: RecipeInputSpec, v: unknown): boolean {
  if (spec.options?.length) return spec.options.some((o) => String(o) === String(v))
  if (spec.type === 'int' || spec.type === 'float') {
    const n = Number(v)
    if (!Number.isFinite(n) || (spec.type === 'int' && !Number.isInteger(n))) return false
    return (spec.min === undefined || n >= spec.min) && (spec.max === undefined || n <= spec.max)
  }
  if (spec.type === 'bool') return typeof v === 'boolean'
  if (spec.type === 'image' || spec.type === 'mesh') return typeof v === 'string'
  return true
}

/** Which declared recipe inputs a project style would fill (engine/projects.apply_style), in apply order. */
export function styleFills(specs: Record<string, RecipeInputSpec>, style: ProjectStyle): StyleFill[] {
  const out: StyleFill[] = []
  const seen = new Set<string>()
  const fill = (key: string, value: unknown): void => {
    const spec = specs[key]
    if (!spec || empty(value) || seen.has(key) || !accepts(spec, value)) return
    seen.add(key)
    out.push({ key, label: FILL_LABEL[key] ?? spec.label ?? key, value })
  }
  const look = [style.prompt_prefix, style.prompt_suffix].map((s) => s.trim().replace(/^,+|,+$/g, '').trim()).filter(Boolean).join(', ')
  if (specs.style && (specs.style.type ?? 'text') === 'text') fill('style', look)
  else if (look) {
    for (const key of PROMPT_INPUTS) {
      if (specs[key] && !seen.has(key)) {
        seen.add(key)
        out.push({ key, label: FILL_LABEL[key]!, value: look, wraps: true })
      }
    }
  }
  fill('negative', style.negative)
  fill('preset', style.preset)
  if (style.pixel) {
    fill('pixel', style.pixel.size)
    fill('palette', style.pixel.palette)
    fill('colors', style.pixel.colors)
    fill('outline', style.pixel.outline)
    fill('dither', style.pixel.dither)
  }
  fill('palette_image', style.palette_image)
  if (specs.image?.type === 'image') fill('image', style.reference_image)
  for (const [k, v] of Object.entries(style.inputs ?? {})) fill(k, v)
  return out
}

/** Short Vietnamese list for the "Sẽ áp" chip; duplicate labels (prompt + idea) collapse. */
export function fillSummary(fills: StyleFill[], presetName?: (id: string) => string | undefined): string {
  const parts = fills.map((f) => (f.key === 'preset' && typeof f.value === 'string' ? `preset ${presetName?.(f.value) ?? f.value}` : f.label))
  return [...new Set(parts)].join(', ')
}

export function styleIsEmpty(s: ProjectStyle): boolean {
  return (
    !s.prompt_prefix.trim() &&
    !s.prompt_suffix.trim() &&
    !s.negative.trim() &&
    !s.preset &&
    !s.palette_image &&
    !s.reference_image &&
    !Object.keys(s.inputs ?? {}).length &&
    !(s.pixel && Object.values(s.pixel).some((v) => !empty(v)))
  )
}
