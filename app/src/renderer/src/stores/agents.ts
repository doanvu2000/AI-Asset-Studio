import { defineStore } from 'pinia'
import { computed, ref, shallowRef, triggerRef } from 'vue'
import type { AgentActivity, AgentSession } from '@/api/types'
import { api } from './connection'

const KEEP = 80
// Same window as core/studio_core/api/agent.py ACTIVE_S: quiet longer than this = idle, not "controlling".
const ACTIVE_MS = 600_000

/** Claude Code / Codex sessions driving the studio over MCP, fed by `agent.activity` WS events. */
export const useAgents = defineStore('agents', () => {
  const sessions = shallowRef(new Map<string, AgentSession>())
  const events = shallowRef<AgentActivity[]>([])
  const now = ref(Date.now())
  let timer = 0
  let pending: AgentActivity[] | null = null

  function tick(): void {
    now.value = Date.now()
    window.clearTimeout(timer)
    if ([...sessions.value.values()].some((s) => s.connected)) timer = window.setTimeout(tick, 30_000)
  }

  function push(ev: AgentActivity): void {
    if (pending) return void pending.push(ev)
    apply(ev)
  }

  function apply(ev: AgentActivity): void {
    const prev = sessions.value.get(ev.session)
    const s: AgentSession = prev
      ? { ...prev }
      : { session: ev.session, client: ev.client, client_version: ev.client_version, connected_at: ev.ts, last_seen: ev.ts, connected: true, active: true, calls: 0 }
    s.client = ev.client
    s.client_version = ev.client_version
    s.last_seen = ev.ts
    s.connected = ev.phase !== 'disconnect'
    if (ev.phase === 'start') {
      s.calls += 1
      s.current = { tool: ev.tool, summary: ev.summary, run_id: ev.run_id, since: ev.ts }
    } else if (ev.phase === 'wait' && s.current) {
      s.current = { ...s.current, detail: ev.detail, run_id: ev.run_id ?? s.current.run_id }
    } else if (ev.phase === 'end' || ev.phase === 'error' || ev.phase === 'disconnect') {
      delete s.current
    }
    if (ev.run_id) s.last_run_id = ev.run_id
    sessions.value.set(ev.session, s)
    triggerRef(sessions)
    events.value = [...events.value.slice(-(KEEP - 1)), ev]
    tick()
  }

  // WS events that land while the snapshot is in flight are replayed on top of it, or a live call would flicker away.
  async function load(): Promise<void> {
    const buffered: AgentActivity[] = []
    pending = buffered
    try {
      const snap = await api().agentActivity(KEEP)
      sessions.value = new Map(snap.sessions.map((s) => [s.session, s]))
      events.value = snap.events
      const last = snap.events.reduce((m, e) => Math.max(m, e.ts), 0)
      pending = null
      buffered.filter((e) => e.ts > last).forEach(apply)
      tick()
    } finally {
      if (pending === buffered) {
        pending = null
        buffered.forEach(apply)
      }
    }
  }

  const live = computed(() =>
    [...sessions.value.values()].filter((s) => s.connected && now.value - s.last_seen * 1000 < ACTIVE_MS).sort((a, b) => b.last_seen - a.last_seen)
  )
  const busy = computed(() => live.value.some((s) => !!s.current))

  return { sessions, events, live, busy, push, load }
})
