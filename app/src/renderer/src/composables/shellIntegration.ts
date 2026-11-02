import { computed, onBeforeUnmount, watch } from 'vue'
import { useRouter } from 'vue-router'
import type { RunRow, RunStatus, WorkflowStatus } from '@/api/types'
import { STEP_DONE_OK } from '@/api/types'
import type { AppNotification, ProgressMode } from '../../../preload/types'
import { useRuns } from '@/stores/runs'
import { useRecipes } from '@/stores/recipes'
import { useFlows } from '@/stores/flows'
import { notifyEnabled } from './notifyPrefs'

const NOTIFY_ON: ReadonlySet<string> = new Set(['awaiting_human', 'succeeded', 'failed'])
// A batch finishing fires many transitions at once; they are folded into one notification.
const COALESCE_MS = 1500
// Three or more finishing together read as noise, so they become one summary toast.
const SUMMARY_MIN = 3

interface Pending {
  label: string
  status: string
  route: string
  error: string | null
  flow?: boolean
}

/** Taskbar progress + OS notifications driven by the runs store (read-only). */
export function useShellIntegration(): void {
  const bridge = window.studio
  if (!bridge) return
  const runs = useRuns()
  const recipes = useRecipes()
  const flows = useFlows()
  const router = useRouter()

  const runFraction = (id: string): number | null => {
    const steps = runs.stepsOf(id)
    if (!steps.length) return null
    let done = 0
    for (const s of steps) {
      if (STEP_DONE_OK.has(s.status)) done += 1
      else if (s.status === 'running') {
        const p = runs.progress.get(s.id)
        if (p && p.max > 0) done += Math.min(1, p.value / p.max)
      }
    }
    return done / steps.length
  }

  const taskbar = computed<{ value: number | null; mode: ProgressMode }>(() => {
    const active = runs.runList.filter((r) => r.status === 'running' || r.status === 'awaiting_human')
    if (!active.length) return { value: null, mode: 'normal' }
    const running = active.filter((r) => r.status === 'running')
    const pool = running.length ? running : active
    const known = pool.map((r) => runFraction(r.id)).filter((f): f is number => f !== null)
    if (!known.length) return running.length ? { value: 0, mode: 'indeterminate' } : { value: 1, mode: 'paused' }
    const avg = known.reduce((a, b) => a + b, 0) / known.length
    return { value: Math.round(avg * 50) / 50, mode: running.length ? 'normal' : 'paused' }
  })

  watch(taskbar, (t, old) => {
    if (old && t.value === old.value && t.mode === old.mode) return
    bridge.setProgress(t.value, t.mode)
  })

  const label = (r: RunRow): string => r.title || recipes.byId(r.recipe_id)?.name || 'Lượt chạy'
  const seen = new Map<string, RunStatus>()
  const seenFlows = new Map<string, WorkflowStatus>()
  let primed = false
  let primedAt = Date.now() / 1000
  let pending: Pending[] = []
  let timer = 0

  function notifyOne(p: Pending): void {
    const n: AppNotification =
      p.status === 'awaiting_human'
        ? { title: 'Cần bạn duyệt', body: `${p.label} đang chờ bạn chọn hoặc duyệt.`, route: p.flow ? p.route : '/gates' }
        : p.status === 'succeeded'
          ? { title: 'Đã xong', body: `${p.label} đã hoàn tất.`, route: p.route }
          : { title: p.flow ? 'Workflow bị lỗi' : 'Lượt chạy bị lỗi', body: `${p.label}: ${p.error ?? 'mở để xem chi tiết.'}`, route: p.route }
    bridge!.notify(n)
  }

  function flush(): void {
    const batch = pending
    pending = []
    if (!notifyEnabled.value || !batch.length) return
    if (batch.length < SUMMARY_MIN) {
      batch.forEach(notifyOne)
      return
    }
    const count = (s: string): number => batch.filter((b) => b.status === s).length
    const parts = [
      count('succeeded') && `${count('succeeded')} lượt đã xong`,
      count('awaiting_human') && `${count('awaiting_human')} cần duyệt`,
      count('failed') && `${count('failed')} bị lỗi`
    ].filter(Boolean)
    bridge!.notify({ title: 'AI Asset Studio', body: parts.join(' · '), route: count('awaiting_human') ? '/gates' : '/runs' })
  }

  function queue(p: Pending): void {
    pending.push(p)
    window.clearTimeout(timer)
    timer = window.setTimeout(flush, COALESCE_MS)
  }

  watch(
    // runList, not the Map: triggerRef keeps the same Map instance, which a getter watch would ignore.
    () => runs.runList,
    (list) => {
      if (!runs.listLoaded) return
      for (const r of list) {
        const prev = seen.get(r.id)
        seen.set(r.id, r.status)
        // A fully cached run can be created and finish within one event frame, so it is first seen already done.
        const bornDone = prev === undefined && r.created_at >= primedAt
        if (!primed || (prev === undefined && !bornDone) || prev === r.status || !NOTIFY_ON.has(r.status)) continue
        queue({ label: label(r), status: r.status, route: `/runs/${r.id}`, error: r.error })
      }
      if (!primed) primedAt = Date.now() / 1000
      primed = true
    },
    { immediate: true }
  )

  watch(
    () => flows.runList,
    (list) => {
      for (const r of list) {
        const prev = seenFlows.get(r.id)
        seenFlows.set(r.id, r.status)
        const fresh = prev === undefined && r.created_at >= primedAt
        if ((prev === undefined && !fresh) || prev === r.status || !NOTIFY_ON.has(r.status)) continue
        queue({ label: r.title || 'Workflow', status: r.status, route: `/flows/runs/${r.id}`, error: r.error, flow: true })
      }
    }
  )

  const off = bridge.onNavigate((route) => void router.push(route))
  onBeforeUnmount(() => {
    off()
    window.clearTimeout(timer)
    bridge.setProgress(null)
  })
}
