import { computed, ref, watch, type Ref } from 'vue'
import type { ShaderPresetInfo } from '../../../preload/types'

const SHADERS_KEY = 'export:shaders'
export type Tweak = { color?: string; speed?: number }
export type ShaderParams = Record<string, Record<string, number | string>>

function readSaved(): string[] {
  try {
    const v = JSON.parse(localStorage.getItem(SHADERS_KEY) ?? 'null') as unknown
    return Array.isArray(v) ? v.filter((s): s is string => typeof s === 'string') : []
  } catch {
    return []
  }
}

type Conn = { baseUrl: string; token: string }
const conn = ref<Conn | null>(null)
let connAsked = false

// Previews are <img> sources, so the token rides in the query like blob URLs; mock mode has no bridge and shows no thumbnails.
function loadConn(): void {
  if (connAsked) return
  connAsked = true
  void window.studio?.getConnection().then((c) => (conn.value = c)).catch(() => (connAsked = false))
}

export function shaderPreviewUrl(id: string, t: Tweak | undefined, still: boolean): string {
  loadConn()
  const c = conn.value
  if (!c) return ''
  const q = new URLSearchParams()
  if (t?.color) q.set('color', t.color)
  if (t?.speed !== undefined && speedOk(t.speed)) q.set('speed', String(t.speed))
  if (still) q.set('still', '1')
  q.set('t', c.token)
  return `${c.baseUrl}/api/shaders/${encodeURIComponent(id)}/preview?${q}`
}

export const speedOk = (v: number | undefined): boolean => v === undefined || (Number.isFinite(v) && v >= 0 && v <= 20)

// Selection is shared by every export surface (bundle menu, pack dialog) through one storage key.
export function useShaderPicker(list: Ref<ShaderPresetInfo[]>) {
  const picked = ref<string[]>(readSaved())
  const tweaks = ref<Record<string, Tweak>>({})
  const pickedInfo = computed(() => list.value.filter((s) => picked.value.includes(s.id)))
  watch(
    picked,
    (v) => {
      try {
        localStorage.setItem(SHADERS_KEY, JSON.stringify(v))
      } catch {
        /* storage blocked: the choice just won't be remembered */
      }
    },
    { deep: true }
  )

  function toggle(id: string): void {
    picked.value = picked.value.includes(id) ? picked.value.filter((s) => s !== id) : [...picked.value, id]
  }
  function tweak(id: string, key: 'color' | 'speed', v: string | number): void {
    tweaks.value = { ...tweaks.value, [id]: { ...tweaks.value[id], [key]: v } }
  }
  function options(): { shaders?: string[]; shader_params?: ShaderParams } {
    const ids = pickedInfo.value.map((s) => s.id)
    if (!ids.length) return {}
    const params: ShaderParams = {}
    for (const id of ids) {
      const t = tweaks.value[id]
      if (!t) continue
      params[id] = {}
      if (t.color) params[id].color = t.color
      if (t.speed !== undefined && speedOk(t.speed)) params[id].speed = t.speed
    }
    return { shaders: ids, shader_params: params }
  }
  const valid = computed(() => pickedInfo.value.every((s) => speedOk(tweaks.value[s.id]?.speed)))
  return { picked, tweaks, pickedInfo, toggle, tweak, options, valid }
}

export type ShaderPicker = ReturnType<typeof useShaderPicker>
