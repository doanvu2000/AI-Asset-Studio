import { computed, onBeforeUnmount, watch } from 'vue'
import { useRouter } from 'vue-router'
import type { RunRow, RunStatus } from '@/api/types'
import { STEP_DONE_OK } from '@/api/types'
import type { AppNotification, ProgressMode } from '../../../preload/types'
import { useRuns } from '@/stores/runs'
import { useRecipes } from '@/stores/recipes'

const NOTIFY_ON: ReadonlySet<RunStatus> = new Set(['awaiting_human', 'succeeded', 'failed'])
// A batch finishing fires many transitions at once; they are folded into one notification.
const COALESCE_MS = 1500

/** Taskbar progress + OS notifications driven by the runs store (read-only). */
export function useShellIntegration(): void {
  const bridge = window.studio
  if (!bridge) return
  const runs = useRuns()
  const recipes = useRecipes()
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
  let primed = false
  let primedAt = 0
  let pending: Array<{ run: RunRow; status: RunStatus }> = []
  let timer = 0

  function flush(): void {
    const batch = pending
    pending = []
    if (!batch.length) return
    if (batch.length === 1) {
      const { run, status } = batch[0]!
      const n: AppNotification =
        status === 'awaiting_human'
          ? { title: 'Cần bạn duyệt', body: `${label(run)} đang chờ bạn chọn hoặc duyệt.`, route: '/gates' }
          : status === 'succeeded'
            ? { title: 'Đã xong', body: `${label(run)} đã hoàn tất.`, route: `/runs/${run.id}` }
            : { title: 'Lượt chạy bị lỗi', body: `${label(run)}: ${run.error ?? 'mở để xem chi tiết.'}`, route: `/runs/${run.id}` }
      bridge!.notify(n)
      return
    }
    const count = (s: RunStatus): number => batch.filter((b) => b.status === s).length
    const parts = [
      count('succeeded') && `${count('succeeded')} lượt đã xong`,
      count('awaiting_human') && `${count('awaiting_human')} cần duyệt`,
      count('failed') && `${count('failed')} bị lỗi`
    ].filter(Boolean)
    bridge!.notify({ title: 'AI Asset Studio', body: parts.join(' · '), route: count('awaiting_human') ? '/gates' : '/runs' })
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
        pending.push({ run: r, status: r.status })
      }
      if (!primed) primedAt = Date.now() / 1000
      primed = true
      if (pending.length) {
        window.clearTimeout(timer)
        timer = window.setTimeout(flush, COALESCE_MS)
      }
    },
    { immediate: true }
  )

  const off = bridge.onNavigate((route) => void router.push(route))
  onBeforeUnmount(() => {
    off()
    window.clearTimeout(timer)
    bridge.setProgress(null)
  })
}
