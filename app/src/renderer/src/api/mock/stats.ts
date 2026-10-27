import type { DailyStats, RunRow, RunStatsCore, StatsOverview, StatsQuery, StepKindStats, StepRow } from '../types'

const TERMINAL = new Set(['succeeded', 'failed', 'cancelled', 'interrupted'])

function num(v: unknown): number {
  return typeof v === 'number' && Number.isFinite(v) ? v : 0
}

function costOf(meta: Record<string, unknown>): number {
  const own = num(meta.cost_usd) + num(meta.claude_cost_usd) + num(meta.prior_cost_usd)
  const items = Array.isArray(meta.items) ? meta.items : []
  return own + items.reduce<number>((a, it) => {
    if (!it || typeof it !== 'object' || Array.isArray(it)) return a
    const m = it as Record<string, unknown>
    return a + num(m.cost_usd) + num(m.claude_cost_usd)
  }, 0)
}

function unionSpan(steps: StepRow[]): number | null {
  const iv = steps
    .filter((s) => s.started_at != null && s.ended_at != null && s.ended_at >= s.started_at)
    .map((s) => [s.started_at!, s.ended_at!] as const)
    .sort((a, b) => a[0] - b[0] || a[1] - b[1])
  if (!iv.length) return null
  let total = 0
  let [lo, hi] = iv[0]!
  for (const [a, b] of iv.slice(1)) {
    if (a > hi) {
      total += hi - lo
      ;[lo, hi] = [a, b]
    } else hi = Math.max(hi, b)
  }
  return total + hi - lo
}

function span(s: StepRow): number {
  return s.started_at != null && s.ended_at != null && s.ended_at >= s.started_at ? s.ended_at - s.started_at : 0
}

function rank(sorted: number[], p: number): number | null {
  const i = sorted.findIndex((_, k) => k + 1 >= p * sorted.length)
  return i < 0 ? null : (sorted[i] ?? null)
}

interface PerRun {
  run: RunRow
  cost: number
  work: number
  gpu: number
  cached: number
  done: number
  active: number | null
  wall: number | null
}

function rollup(rows: PerRun[]): RunStatsCore {
  const by = (s: string) => rows.filter((r) => r.run.status === s).length
  const ok = rows.filter((r) => r.run.status === 'succeeded')
  const dur = ok.map((r) => r.active).filter((v): v is number => v != null).sort((a, b) => a - b)
  const walls = ok.map((r) => r.wall).filter((v): v is number => v != null)
  const failed = by('failed') + by('interrupted')
  const cost = rows.reduce((a, r) => a + r.cost, 0)
  const cached = rows.reduce((a, r) => a + r.cached, 0)
  const done = rows.reduce((a, r) => a + r.done, 0)
  const avg = (xs: number[]) => (xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : null)
  return {
    runs: rows.length,
    succeeded: ok.length,
    failed: by('failed'),
    cancelled: by('cancelled'),
    interrupted: by('interrupted'),
    active: by('running') + by('awaiting_human'),
    failure_rate: ok.length + failed ? failed / (ok.length + failed) : null,
    cost_usd: cost,
    avg_cost_usd: rows.length ? cost / rows.length : 0,
    runs_with_cost: rows.filter((r) => r.cost > 0).length,
    duration_s: { avg: avg(dur), p50: rank(dur, 0.5), p90: rank(dur, 0.9) },
    wall_avg_s: avg(walls),
    work_s: rows.reduce((a, r) => a + r.work, 0),
    gpu_s: rows.reduce((a, r) => a + r.gpu, 0),
    cache_hit_ratio: done ? cached / done : null,
    last_run_at: rows.length ? Math.max(...rows.map((r) => r.run.created_at)) : null
  }
}

function localDay(ts: number, off: number): string {
  return new Date((ts + off) * 1000).toISOString().slice(0, 10)
}

/** Same shapes and rules as core `/api/stats/overview`, computed over the mock's in-memory rows. */
export function mockStatsOverview(runs: RunRow[], steps: StepRow[], q: StatsQuery = {}): StatsOverview {
  const off = q.tz_offset_s ?? -new Date().getTimezoneOffset() * 60
  const end = q.until != null ? q.until - 0.001 : Date.now() / 1000
  const scoped = runs.filter((r) => !q.project_id || r.project_id === q.project_id)
  const first = q.since ?? (scoped.length ? Math.min(...scoped.map((r) => r.created_at)) : null)
  const nDays = q.days ?? (first == null ? 30 : Math.max(7, Math.min(366, Math.ceil((end - first) / 86400))))
  const lastDay = localDay(end, off)
  const dayStart = Date.parse(`${lastDay}T00:00:00Z`) / 1000 - off - (nDays - 1) * 86400
  if (q.since != null) q = { ...q, since: dayStart }
  const inRange = runs.filter(
    (r) =>
      (q.since == null || r.created_at >= q.since) &&
      (q.until == null || r.created_at < q.until) &&
      (!q.project_id || r.project_id === q.project_id)
  )
  const ids = new Set(inRange.map((r) => r.id))
  const stepsOf = new Map<string, StepRow[]>()
  for (const s of steps) if (ids.has(s.run_id)) stepsOf.set(s.run_id, [...(stepsOf.get(s.run_id) ?? []), s])
  const per: PerRun[] = inRange.map((run) => {
    const ss = stepsOf.get(run.id) ?? []
    const work = ss.filter((s) => s.lane !== 'none')
    const ran = work.filter((s) => s.status === 'succeeded' || s.status === 'failed')
    const wall = TERMINAL.has(run.status) && run.ended_at != null && run.ended_at >= run.created_at ? run.ended_at - run.created_at : null
    return {
      run,
      cost: ss.reduce((a, s) => a + costOf(s.meta), 0),
      work: ran.reduce((a, s) => a + span(s), 0),
      gpu: ran.filter((s) => s.lane === 'gpu').reduce((a, s) => a + span(s), 0),
      cached: work.filter((s) => s.status === 'cached').length,
      done: work.filter((s) => s.status === 'succeeded' || s.status === 'cached').length,
      active: TERMINAL.has(run.status) ? unionSpan(work.filter((s) => ['succeeded', 'failed', 'cached'].includes(s.status))) : null,
      wall
    }
  })
  const groups = new Map<string, PerRun[]>()
  for (const p of per) groups.set(p.run.recipe_id, [...(groups.get(p.run.recipe_id) ?? []), p])
  const recipes = [...groups].map(([recipe_id, rows]) => ({ recipe_id, ...rollup(rows) })).sort((a, b) => b.cost_usd - a.cost_usd || b.runs - a.runs)

  const kinds = new Map<string, StepKindStats & { okTotal: number; okN: number }>()
  for (const ss of stepsOf.values())
    for (const s of ss) {
      if (s.lane === 'none') continue
      const k = kinds.get(s.kind) ?? { kind: s.kind, steps: 0, failed: 0, cached: 0, cost_usd: 0, total_s: 0, avg_s: null, okTotal: 0, okN: 0 }
      if (s.status === 'succeeded' || s.status === 'failed' || s.status === 'cached') k.steps += 1
      k.failed += Number(s.status === 'failed')
      k.cached += Number(s.status === 'cached')
      k.cost_usd += costOf(s.meta)
      if (s.status === 'succeeded' || s.status === 'failed') k.total_s += span(s)
      if (s.status === 'succeeded') {
        k.okTotal += span(s)
        k.okN += 1
      }
      kinds.set(s.kind, k)
    }
  const stepRows = [...kinds.values()]
    .map(({ okTotal, okN, ...k }) => ({ ...k, avg_s: okN ? okTotal / okN : null }))
    .sort((a, b) => b.cost_usd - a.cost_usd || b.total_s - a.total_s)
    .filter((k) => k.steps > 0 || k.cost_usd > 0)
    .slice(0, 8)

  const days: DailyStats[] = []
  for (let i = nDays - 1; i >= 0; i--) {
    const day = localDay(end - i * 86400, off)
    const rows = per.filter((p) => localDay(p.run.created_at, off) === day)
    const dur = rows.filter((p) => p.run.status === 'succeeded' && p.active != null).map((p) => p.active!)
    days.push({
      day,
      runs: rows.length,
      succeeded: rows.filter((p) => p.run.status === 'succeeded').length,
      failed: rows.filter((p) => p.run.status === 'failed' || p.run.status === 'interrupted').length,
      cost_usd: rows.reduce((a, p) => a + p.cost, 0),
      work_s: rows.reduce((a, p) => a + p.work, 0),
      duration_avg_s: dur.length ? dur.reduce((a, b) => a + b, 0) / dur.length : null
    })
  }
  return {
    since: q.since ?? null,
    until: q.until ?? null,
    project_id: q.project_id ?? null,
    totals: rollup(per),
    recipes,
    steps: stepRows,
    daily: { days, tz_offset_s: off, since: dayStart, until: dayStart + nDays * 86400 }
  }
}
