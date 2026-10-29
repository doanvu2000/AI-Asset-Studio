<script setup lang="ts">
import { computed, nextTick, ref, useId } from 'vue'
import { useRouter } from 'vue-router'
import { onClickOutside, useEventListener } from '@vueuse/core'
import { ChevronDown, Clapperboard, Forward, LoaderCircle, PersonStanding } from '@lucide/vue'
import type { BlobRef, Outputs, RunRow } from '@/api/types'
import { api } from '@/stores/connection'
import { useRecipes } from '@/stores/recipes'
import { useToasts } from '@/stores/toasts'
import { followUps, prefillQuery, type FollowUp } from '@/composables/followUp'

const props = defineProps<{ hash: string; blob?: BlobRef; runId?: string; stepId?: string; compact?: boolean }>()

const recipes = useRecipes()
const router = useRouter()
const toasts = useToasts()
const uid = useId()
const open = ref(false)
const loading = ref(false)
const starting = ref(false)
const active = ref(0)
const button = ref<HTMLButtonElement | null>(null)
const menu = ref<HTMLElement | null>(null)
const pos = ref({ left: 0, top: 0 })
const clicked = ref<BlobRef | null>(null)
const siblings = ref<Outputs>({})
const run = ref<RunRow | null>(null)

// The character kit chains 8-dir → Spine in core (`$meta.then`), so it keeps going even if the app is closed.
const KIT = { first: 'sprite-8dir-2d', then: 'spine-character-image' }
const kitOk = computed(
  () =>
    !!clicked.value?.mime.startsWith('image/') &&
    /^(concept|variants)-/.test(run.value?.recipe_id ?? '') &&
    !!recipes.byId(KIT.first) &&
    !!recipes.byId(KIT.then)
)

// A still character image goes straight to a sprite animation with the action already chosen.
const ANIM = 'sprite-anim-video'
const ANIM_ACTIONS = [
  { id: 'idle', label: 'Đứng thở' },
  { id: 'walk', label: 'Đi' },
  { id: 'run', label: 'Chạy' },
  { id: 'slash', label: 'Chém' },
  { id: 'jump', label: 'Nhảy' },
  { id: 'hit', label: 'Bị đánh' },
  { id: 'cast', label: 'Phép' }
] as const

type Item = { id: string; label: string; hint?: string; kit?: boolean; anim?: boolean; follow?: FollowUp }
const items = computed<Item[]>(() => {
  const list: Item[] = kitOk.value ? [{ id: 'kit', label: 'Bộ nhân vật', hint: '8 hướng → Spine', kit: true }] : []
  if (clicked.value) {
    const all = followUps(recipes.list, clicked.value, siblings.value)
    const anim = all.find((f) => f.recipe.id === ANIM && !f.missing.length)
    if (anim) list.push({ id: ANIM, label: 'Hoạt ảnh sprite', hint: 'video AI → sheet', anim: true, follow: anim })
    for (const f of all)
      if (f !== anim)
        list.push({ id: f.recipe.id, label: f.recipe.name, hint: f.missing.length ? `thiếu ${f.missing.join(', ')}` : undefined, follow: f })
  }
  return list
})
const itemId = (i: number): string => `${uid}-fu-${i}`

async function loadContext(): Promise<void> {
  loading.value = true
  try {
    await recipes.load()
    let found: BlobRef | null = props.blob ?? null
    if (props.runId) {
      const d = await api().getRun(props.runId)
      run.value = d.run
      const step = d.steps.find((s) => s.id === props.stepId && hasHash(s.outputs)) ?? d.steps.find((s) => hasHash(s.outputs))
      siblings.value = step?.outputs ?? {}
      found = found ?? Object.values(siblings.value).flat().find((b) => b.hash === props.hash) ?? null
    }
    clicked.value = found ?? (await api().blobInfo(props.hash))
  } catch (e) {
    toasts.error(e, 'Không đọc được thông tin kết quả')
  } finally {
    loading.value = false
  }
}
const hasHash = (o: Outputs): boolean => Object.values(o).some((l) => l.some((b) => b.hash === props.hash))

async function show(): Promise<void> {
  const r = button.value?.getBoundingClientRect()
  if (!r) return
  pos.value = { left: Math.min(r.left, window.innerWidth - 308), top: r.bottom + 4 }
  active.value = 0
  open.value = true
  if (clicked.value?.hash !== props.hash) {
    clicked.value = null
    siblings.value = {}
    run.value = null
    await loadContext()
  }
  await nextTick()
  menu.value?.focus()
}

function close(refocus = true): void {
  if (!open.value) return
  open.value = false
  if (refocus) button.value?.focus()
}

async function startKit(): Promise<void> {
  if (starting.value) return
  starting.value = true
  try {
    const { run_id } = await api().startRun({
      recipe_id: KIT.first,
      inputs: { image: props.hash },
      title: run.value?.title || undefined,
      project_id: run.value?.project_id,
      params: { $meta: { source: 'app', then: [{ recipe_id: KIT.then, map: { image: 'inputs.image' } }] } }
    })
    toasts.push('Đã bắt đầu bộ nhân vật: Spine tự chạy sau khi 8 hướng xong', 'success', 3200)
    await router.push(`/runs/${run_id}`)
  } catch (e) {
    toasts.error(e, 'Không bắt đầu được bộ nhân vật')
  } finally {
    starting.value = false
  }
}

function choose(i: number, action?: string): void {
  const it = items.value[i]
  if (!it) return
  close(false)
  if (it.kit) return void startKit()
  if (!it.follow) return
  const query = prefillQuery(it.follow.recipe.id, it.follow.prefill, run.value?.project_id)
  if (action) query['input.action'] = action
  void router.push({ path: '/new', query })
}

function onKey(e: KeyboardEvent): void {
  const n = items.value.length
  const keys: Record<string, () => void> = {
    ArrowDown: () => (active.value = (active.value + 1) % n),
    ArrowUp: () => (active.value = (active.value - 1 + n) % n),
    Home: () => (active.value = 0),
    End: () => (active.value = n - 1),
    Enter: () => choose(active.value),
    ' ': () => choose(active.value),
    Escape: () => close()
  }
  if (e.key === 'Tab') return close(false)
  const fn = keys[e.key]
  if (!fn || (!n && e.key !== 'Escape')) return
  e.preventDefault()
  e.stopPropagation()
  fn()
}

onClickOutside(menu, () => close(false), { ignore: [button] })
useEventListener(window, 'resize', () => close(false))
useEventListener(window, 'scroll', () => close(false), { capture: true, passive: true })
</script>

<template>
  <button
    ref="button"
    type="button"
    class="btn btn-plain"
    :class="{ 'btn-icon': compact }"
    :disabled="starting"
    aria-haspopup="menu"
    :aria-expanded="open"
    :aria-label="compact ? 'Dùng tiếp cho…' : undefined"
    title="Dùng kết quả này làm đầu vào cho quy trình khác"
    @click="open ? close() : show()"
  >
    <LoaderCircle v-if="starting" class="spin" />
    <Forward v-else />
    <span v-if="!compact">Dùng tiếp cho…</span>
    <ChevronDown v-if="!compact" :size="13" class="chev" :class="{ up: open }" aria-hidden="true" />
  </button>
  <Teleport to="body">
    <Transition name="menu">
      <div
        v-if="open"
        ref="menu"
        class="f-menu"
        role="menu"
        tabindex="-1"
        aria-label="Dùng tiếp cho"
        :aria-activedescendant="items.length ? itemId(active) : undefined"
        :style="{ left: `${pos.left}px`, top: `${pos.top}px` }"
        @keydown="onKey"
      >
        <p class="f-head caption">Dùng tiếp cho</p>
        <p v-if="loading" class="f-empty caption"><LoaderCircle :size="13" class="spin" /> Đang tìm quy trình phù hợp…</p>
        <p v-else-if="!items.length" class="f-empty caption">Chưa có quy trình nào nhận loại tệp này</p>
        <template v-for="(it, i) in items" :key="it.id">
          <hr v-if="i > 0 && (items[i - 1]?.kit || items[i - 1]?.anim)" class="f-sep" />
          <div :id="itemId(i)" role="menuitem" class="f-item" :class="{ active: i === active, kit: it.kit || it.anim, anim: it.anim }" @mousemove="active = i" @click="choose(i)">
            <PersonStanding v-if="it.kit" :size="14" aria-hidden="true" />
            <Clapperboard v-else-if="it.anim" :size="14" aria-hidden="true" />
            <span class="f-label">{{ it.label }}</span>
            <span v-if="it.hint" class="f-hint">{{ it.hint }}</span>
            <span v-if="it.anim" class="f-chips">
              <button v-for="a in ANIM_ACTIONS" :key="a.id" type="button" class="f-chip" tabindex="-1" @click.stop="choose(i, a.id)">{{ a.label }}</button>
            </span>
          </div>
        </template>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.chev {
  margin-left: -2px;
  color: var(--text-3);
  transition: rotate var(--t-fast) var(--ease-out);
}
.chev.up {
  rotate: 180deg;
}
.f-menu {
  position: fixed;
  z-index: 60;
  width: 300px;
  max-height: min(420px, 70vh);
  overflow-y: auto;
  padding: 5px;
  border-radius: var(--r-md);
  background: var(--bg-elevated);
  box-shadow: var(--shadow-2), var(--hairline);
  font-size: var(--fs-body);
  color: var(--text);
  outline: none;
  transform-origin: top left;
}
.f-head {
  margin: 2px 8px 4px;
  color: var(--text-3);
}
.f-empty {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 4px 8px 6px;
  color: var(--text-2);
}
.f-sep {
  margin: 4px 6px;
  border: 0;
  border-top: 1px solid var(--separator);
}
.f-item {
  display: flex;
  align-items: center;
  gap: 8px;
  min-height: 28px;
  padding: 3px 10px;
  border-radius: var(--r-sm);
  cursor: default;
}
.f-item.kit {
  font-weight: 600;
}
.f-item.active {
  background: var(--accent);
  color: var(--text-on-accent);
}
.f-item.anim {
  flex-wrap: wrap;
  row-gap: 4px;
  padding-bottom: 6px;
}
.f-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  flex-basis: 100%;
  padding-left: 22px;
}
.f-chip {
  padding: 1px 8px;
  border: 0;
  border-radius: 999px;
  background: var(--bg-active);
  color: inherit;
  font: inherit;
  font-size: var(--fs-caption);
  font-weight: 500;
  cursor: default;
  transition:
    background var(--t-fast) var(--ease-out),
    scale var(--t-fast) var(--ease-out);
}
.f-item.active .f-chip {
  background: rgba(255, 255, 255, 0.2);
}
.f-item.active .f-chip:hover {
  background: rgba(255, 255, 255, 0.34);
}
.f-chip:active {
  scale: 0.95;
}
.f-label {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.f-hint {
  max-width: 45%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: var(--fs-caption);
  color: var(--text-3);
}
.f-item.active .f-hint {
  color: inherit;
  opacity: 0.85;
}
.spin {
  animation: spin 1s linear infinite;
}
.menu-enter-active {
  transition:
    opacity var(--t-fast) var(--ease-out),
    scale var(--t-fast) var(--ease-out);
}
.menu-leave-active {
  transition: opacity 90ms var(--ease-in);
}
.menu-enter-from {
  opacity: 0;
  scale: 0.97;
}
.menu-leave-to {
  opacity: 0;
}
@media (prefers-reduced-motion: reduce) {
  .chev,
  .f-chip,
  .menu-enter-active,
  .menu-leave-active {
    transition: none;
  }
}
</style>
