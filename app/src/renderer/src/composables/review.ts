import type { BlobRef, StepRow } from '@/api/types'
import { blobUrl } from '@/stores/connection'

export type ReviewTier = 'good' | 'warn' | 'bad'

export interface StepReview {
  step: StepRow
  score: number
  critique: string
  history: number[]
}

export interface ReviewRound {
  round: number
  score: number | null
  critique: string
  spec: unknown
  nextSpec: unknown
  diff: SpecChange[]
  strip: string | null
  zoom?: string | null
  zoomBare?: string | null
  seamVisible?: boolean
  bend?: { bone: string; time: number; total: number; limit: number } | null
  knee?: KneeGap | null
  kneeZoom?: string | null
  bounce?: Array<[number, number]> | null
  heldView?: string | null
  handZoom?: string | null
  hand?: { bone: string; time: number } | null
  skirtViews?: Array<{ skin: string; image: string }>
  held?: { bone: string; time: number; skin: string } | null
  error?: string
}

export interface KneeGap {
  knees: [string, string]
  setup_gap: number
  min_gap: number
  time: number
  ratio: number
  overlap: boolean
}

export interface SpecChange {
  path: string
  old: unknown
  new: unknown
}

export function reviewTier(score: number): ReviewTier {
  return score >= 8 ? 'good' : score >= 6 ? 'warn' : 'bad'
}

/** Latest step with a numeric `meta.score` (claude.task with a score schema, review loop) before `beforeOrder`. */
export function latestReview(steps: StepRow[], beforeOrder?: number): StepReview | null {
  let found: StepReview | null = null
  for (const s of steps) {
    if (beforeOrder !== undefined && s.order >= beforeOrder) break
    const m = s.meta ?? {}
    if (typeof m.score !== 'number') continue
    found = {
      step: s,
      score: m.score,
      critique: typeof m.critique === 'string' ? m.critique : '',
      history: Array.isArray(m.score_history) ? (m.score_history as unknown[]).filter((x): x is number => typeof x === 'number') : []
    }
  }
  return found
}

export async function blobJson<T = unknown>(hash: string): Promise<T> {
  const r = await fetch(blobUrl(hash))
  if (!r.ok) throw new Error(`HTTP ${r.status}`)
  return (await r.json()) as T
}

export function diffSpec(old: unknown, next: unknown, path = ''): SpecChange[] {
  const isObj = (v: unknown): v is Record<string, unknown> => !!v && typeof v === 'object' && !Array.isArray(v)
  if (isObj(old) && isObj(next)) {
    const keys = [...new Set([...Object.keys(old), ...Object.keys(next)])]
    return keys.flatMap((k) => diffSpec(old[k], next[k], path ? `${path}.${k}` : k))
  }
  return JSON.stringify(old) === JSON.stringify(next) ? [] : [{ path, old: old ?? null, new: next ?? null }]
}

/** Rounds of a review: the loop's review.json, or one round built from a plain claude.task result. */
export async function loadRounds(review: StepReview, oldSpec: unknown): Promise<ReviewRound[]> {
  const out = review.step.outputs
  const reviewBlob = out.review?.[0]
  if (reviewBlob) {
    const doc = await blobJson<{ rounds: Array<Record<string, unknown>> }>(reviewBlob.hash)
    return doc.rounds.map((r) => ({
      round: Number(r.round),
      score: typeof r.score === 'number' ? r.score : null,
      critique: String(r.critique ?? r.error ?? ''),
      spec: r.spec,
      nextSpec: r.next_spec,
      diff: (r.diff as SpecChange[] | undefined) ?? [],
      strip: typeof r.strip === 'string' ? r.strip : null,
      zoom: typeof r.zoom === 'string' ? r.zoom : null,
      zoomBare: typeof r.zoom_bare === 'string' ? r.zoom_bare : null,
      seamVisible: r.seam_visible === true,
      bend: (r.deepest_bend as ReviewRound['bend']) ?? null,
      knee: (r.knee as KneeGap | undefined) ?? null,
      kneeZoom: typeof r.knee_zoom === 'string' ? r.knee_zoom : null,
      heldView: typeof r.held_view === 'string' ? r.held_view : null,
      handZoom: typeof r.hand_zoom === 'string' ? r.hand_zoom : null,
      hand: (r.hand as ReviewRound['hand']) ?? null,
      skirtViews: Array.isArray(r.skirt_views) ? (r.skirt_views as ReviewRound['skirtViews']) : [],
      held: (r.held as ReviewRound['held']) ?? null,
      bounce: Array.isArray(r.bounce) ? (r.bounce as Array<[number, number]>) : null,
      error: typeof r.error === 'string' ? r.error : undefined
    }))
  }
  const jsonBlob = out.json?.[0]
  const result = jsonBlob ? await blobJson<Record<string, unknown>>(jsonBlob.hash) : {}
  const spec = result.spec ?? null
  return [{ round: 1, score: review.score, critique: review.critique, spec: oldSpec, nextSpec: spec, diff: oldSpec ? diffSpec(oldSpec, spec) : [], strip: null }]
}

/** Images a reviewer looked at: reference sheets (`ref`) and, for a plain claude.task, its input images. */
export function reviewImages(review: StepReview): { refs: BlobRef[]; inputs: BlobRef[] } {
  const i = review.step.inputs
  return { refs: i.ref ?? [], inputs: i.images ?? [] }
}

const esc = (s: string): string => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
const inline = (s: string): string =>
  esc(s)
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    .replace(/(^|\W)\*([^*\s][^*]*)\*(?=\W|$)/g, '$1<em>$2</em>')

/** Small Markdown subset for critiques (headings, lists, bold/italic/code, paragraphs); input is escaped first. */
export function miniMarkdown(md: string): string {
  const html: string[] = []
  let list: 'ul' | 'ol' | null = null
  let para: string[] = []
  const flush = (): void => {
    if (para.length) html.push(`<p>${inline(para.join(' '))}</p>`)
    para = []
  }
  const close = (): void => {
    if (list) html.push(`</${list}>`)
    list = null
  }
  for (const raw of md.split(/\r?\n/)) {
    const line = raw.trimEnd()
    const h = /^(#{1,4})\s+(.*)$/.exec(line)
    const ul = /^\s*[-*•]\s+(.*)$/.exec(line)
    const ol = /^\s*\d+[.)]\s+(.*)$/.exec(line)
    if (!line.trim()) {
      flush()
      close()
    } else if (h) {
      flush()
      close()
      html.push(`<h4>${inline(h[2]!)}</h4>`)
    } else if (ul || ol) {
      flush()
      const want = ul ? 'ul' : 'ol'
      if (list !== want) {
        close()
        html.push(`<${want}>`)
        list = want
      }
      html.push(`<li>${inline((ul ?? ol)![1]!)}</li>`)
    } else {
      close()
      para.push(line.trim())
    }
  }
  flush()
  close()
  return html.join('')
}
