<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, useTemplateRef } from 'vue'
import { useRouter } from 'vue-router'
import { onClickOutside, onKeyStroke } from '@vueuse/core'
import { ChevronRight, Ellipsis, RotateCcw } from '@lucide/vue'
import { api } from '@/stores/connection'
import { useToasts } from '@/stores/toasts'
import { rerunRun, type RerunSeed } from '@/composables/rerun'
import type { RunRow, StepRow } from '@/api/types'
import { RUN_ACTIVE } from '@/api/types'
import { humanDuration, STEP_LABEL, stepName } from '@/composables/format'
import { recipeArt } from '@/assets/art'
import { rememberOrigin } from '@/composables/motion'
import { useRecipes } from '@/stores/recipes'
import { useRuns } from '@/stores/runs'
import RunStatusBadge from './RunStatusBadge.vue'
import ElapsedTimer from './ElapsedTimer.vue'
import EtaChip from './EtaChip.vue'
import RelTime from './RelTime.vue'
import BlobImage from './BlobImage.vue'
import ScoreBadge from './ScoreBadge.vue'
import { latestReview } from '@/composables/review'

const props = defineProps<{ run: RunRow; steps?: StepRow[]; menu?: boolean; selectable?: boolean; selected?: boolean }>()
const emit = defineEmits<{ select: [] }>()
const recipes = useRecipes()
const runs = useRuns()

const recipeName = computed(() => recipes.byId(props.run.recipe_id)?.name ?? (recipes.loaded ? 'Quy trình đã xoá' : ''))
const active = computed(() => RUN_ACTIVE.has(props.run.status))
const current = computed(() =>
  props.steps?.find((s) => ['running', 'waiting_resource', 'awaiting_human', 'failed'].includes(s.status))
)
const live = computed(() => (props.run.status === 'running' ? runs.livePreview(props.run.id) : null))
const hash = computed(() => runs.previewHash(props.run.id))
const art = computed(() => recipeArt(props.run.recipe_id))
const thumb = useTemplateRef<HTMLElement>('thumb')
const review = computed(() => (props.steps ? latestReview(props.steps) : null))

const router = useRouter()
const toasts = useToasts()
const canRerun = computed(() => !!props.menu && !active.value)
const open = ref(false)
const busy = ref(false)
// Steps are not in the list payload, so the seed question is answered when the menu opens.
const seeds = ref<'loading' | 'yes' | 'no'>('loading')
const trigger = useTemplateRef<HTMLButtonElement>('trigger')
const popup = useTemplateRef<HTMLElement>('popup')
const pos = ref({ top: 0, right: 0 })
const close = (): void => void (open.value = false)
onClickOutside(popup, close, { ignore: [trigger] })
onKeyStroke('Escape', () => {
  if (!open.value) return
  close()
  trigger.value?.focus()
})
window.addEventListener('resize', close)
window.addEventListener('wheel', close, { passive: true })
onBeforeUnmount(() => {
  window.removeEventListener('resize', close)
  window.removeEventListener('wheel', close)
})

function onRowClick(e: MouseEvent): void {
  if (props.selectable && e.shiftKey) {
    e.preventDefault()
    emit('select')
    return
  }
  if (!live.value && hash.value) rememberOrigin(props.run.id, thumb.value)
}

async function toggle(): Promise<void> {
  if (open.value) return close()
  const r = trigger.value?.getBoundingClientRect()
  if (r) pos.value = { top: Math.min(r.bottom + 4, window.innerHeight - 96), right: window.innerWidth - r.right }
  open.value = true
  seeds.value = 'loading'
  await nextTick()
  popup.value?.querySelector<HTMLElement>('[role=menuitem]')?.focus()
  try {
    const d = await api().getRun(props.run.id)
    seeds.value = d.steps.some((s) => typeof s.meta.seed_used === 'number') ? 'yes' : 'no'
  } catch {
    seeds.value = 'no'
  }
}

function onMenuKey(e: KeyboardEvent): void {
  if (e.key !== 'ArrowDown' && e.key !== 'ArrowUp') return
  const items = [...(popup.value?.querySelectorAll<HTMLButtonElement>('[role=menuitem]:not(:disabled)') ?? [])]
  if (!items.length) return
  e.preventDefault()
  const i = items.indexOf(document.activeElement as HTMLButtonElement)
  items[(i + (e.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length]!.focus()
}

async function rerun(seed: RerunSeed): Promise<void> {
  if (busy.value) return
  busy.value = true
  close()
  try {
    const { run_id } = await rerunRun(props.run.id, seed)
    toasts.push(seed === 'same' ? 'Đã chạy lại với seed cũ' : 'Đã chạy lại với seed mới', 'success', 2500)
    void router.push({ name: 'run', params: { id: run_id } })
  } catch (e) {
    toasts.error(e, 'Không chạy lại được')
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div class="rw" :class="{ 'has-menu': canRerun, selectable, picked: selected }">
  <label v-if="selectable" class="pick" :title="selected ? 'Bỏ chọn lượt này' : 'Chọn để so sánh (Shift+bấm vào dòng cũng được)'">
    <input type="checkbox" :checked="selected" :aria-label="`Chọn lượt ${run.title || recipeName} để so sánh`" @change="emit('select')" />
  </label>
  <RouterLink :to="`/runs/${run.id}`" class="row" @click="onRowClick">
    <span ref="thumb" class="thumb checker" aria-hidden="true">
      <img v-if="live" class="live" :src="live" alt="" draggable="false" />
      <BlobImage v-else-if="hash" :hash="hash" :size="96" />
      <img v-else-if="art" class="art" :src="art" alt="" draggable="false" />
    </span>
    <div class="main">
      <div class="l1">
        <span class="title-text" :title="run.title || recipeName">{{ run.title || recipeName }}</span>
        <RunStatusBadge :status="run.status" />
        <EtaChip v-if="run.status === 'running'" :run="run" :steps="steps" />
        <ScoreBadge v-if="review" :score="review.score" :history="review.history" />
      </div>
      <div class="l2 caption">
        <span class="recipe" :title="recipeName">{{ recipeName }}</span>
        <span>·</span>
        <RelTime :ts="run.created_at" />
        <template v-if="current">
          <span>·</span>
          <span class="cur" :class="`c-${current.status}`" :title="stepName(current)">{{ stepName(current) }}</span>
        </template>
      </div>
    </div>
    <div v-if="steps?.length" class="dots">
      <span
        v-for="s in steps"
        :key="s.id"
        class="d"
        :class="`d-${s.status}`"
        :title="`${stepName(s)}: ${STEP_LABEL[s.status]}`"
        role="img"
        :aria-label="`${stepName(s)}: ${STEP_LABEL[s.status]}`"
      />
    </div>
    <span class="time num caption">
      <ElapsedTimer v-if="active" :from="run.created_at" />
      <template v-else-if="run.ended_at">{{ humanDuration(run.ended_at - run.created_at) }}</template>
    </span>
    <ChevronRight class="chev" :size="16" />
  </RouterLink>
  <button
    v-if="canRerun"
    ref="trigger"
    type="button"
    class="more"
    aria-haspopup="menu"
    :aria-expanded="open"
    :disabled="busy"
    aria-label="Tuỳ chọn lượt chạy"
    title="Chạy lại"
    @click="toggle"
  >
    <Ellipsis :size="16" />
  </button>
  <Teleport to="body">
    <Transition name="menu-pop">
      <div
        v-if="open"
        ref="popup"
        class="run-menu"
        role="menu"
        aria-label="Chạy lại"
        :style="{ top: `${pos.top}px`, right: `${pos.right}px` }"
        @keydown="onMenuKey"
      >
        <button type="button" role="menuitem" class="mi" @click="rerun('new')"><RotateCcw :size="15" /> Chạy lại, seed mới</button>
        <button
          type="button"
          role="menuitem"
          class="mi"
          :disabled="seeds !== 'yes'"
          :title="seeds === 'no' ? 'Lượt chạy này không có bước nào dùng seed' : 'Cùng thiết lập và cùng seed: kết quả trước được dùng lại nếu có thể'"
          @click="rerun('same')"
        >
          <RotateCcw :size="15" /> Chạy lại, giữ seed
        </button>
      </div>
    </Transition>
  </Teleport>
  </div>
</template>

<style scoped>
.rw {
  position: relative;
  height: 100%;
}
.rw.selectable {
  display: flex;
  align-items: center;
}
.rw.selectable .row {
  flex: 1;
  min-width: 0;
}
.rw.picked {
  background: var(--accent-soft);
}
.pick {
  flex: none;
  display: grid;
  place-items: center;
  width: 36px;
  height: 100%;
  cursor: pointer;
}
.pick input {
  width: 16px;
  height: 16px;
  margin: 0;
  accent-color: var(--accent);
  cursor: pointer;
}
.rw.has-menu .row {
  padding-right: 52px;
}
.more {
  position: absolute;
  top: 50%;
  right: 12px;
  transform: translateY(-50%);
  display: grid;
  place-items: center;
  width: 28px;
  height: 28px;
  border: 0;
  border-radius: var(--r-sm);
  background: transparent;
  color: var(--text-2);
  cursor: pointer;
  transition: background-color var(--t-fast), color var(--t-fast);
}
.more:hover,
.more[aria-expanded='true'] {
  background: var(--bg-active);
  color: var(--text);
}
.more:disabled {
  opacity: 0.5;
  cursor: default;
}
.row {
  display: flex;
  align-items: center;
  gap: 12px;
  height: 100%;
  padding: 0 14px 0 8px;
  color: inherit;
  text-decoration: none;
  border-radius: var(--r-md);
  transition: background-color var(--t-fast);
}
.row:hover {
  background: var(--bg-hover);
}
.thumb {
  position: relative;
  flex: none;
  width: 40px;
  height: 40px;
  border-radius: 8px;
  overflow: hidden;
  box-shadow: var(--hairline);
  background-size: 8px 8px;
  background-position: 0 0, 0 4px, 4px -4px, -4px 0;
}
.thumb :deep(.blob) {
  width: 100%;
  height: 100%;
}
.thumb img {
  width: 100%;
  height: 100%;
  object-fit: contain;
}
.thumb .art {
  padding: 3px;
  opacity: 0.9;
}
.main {
  flex: 1;
  min-width: 0;
  display: grid;
  gap: 2px;
}
.l1 {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
}
.title-text {
  font-weight: 600;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.l2 {
  display: flex;
  gap: 6px;
  white-space: nowrap;
  overflow: hidden;
}
.recipe {
  overflow: hidden;
  text-overflow: ellipsis;
}
.cur {
  font-weight: 600;
}
.c-running {
  color: var(--st-running);
}
.c-waiting_resource {
  color: var(--st-waiting);
}
.c-awaiting_human {
  color: var(--st-human);
}
.c-failed {
  color: var(--st-fail);
}
.dots {
  display: flex;
  gap: 4px;
}
.d {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--bg-active);
  transition: background-color var(--t-base);
}
.d-running {
  background: var(--st-running);
  animation: soft-pulse 1.4s ease-in-out infinite;
}
.d-waiting_resource {
  background: var(--st-waiting);
}
.d-awaiting_human {
  background: var(--st-human);
}
.d-succeeded {
  background: var(--st-ok);
}
.d-cached {
  background: var(--st-cached);
}
.d-failed {
  background: var(--st-fail);
}
.time {
  min-width: 64px;
  text-align: right;
}
.chev {
  color: var(--text-3);
  flex: none;
}
.rw.has-menu .chev {
  display: none;
}
.run-menu {
  position: fixed;
  z-index: 60;
  min-width: 200px;
  display: grid;
  padding: 5px;
  border-radius: var(--r-md);
  background: var(--bg-elevated);
  box-shadow: var(--shadow-2), var(--hairline);
  transform-origin: top right;
}
.mi {
  display: flex;
  align-items: center;
  gap: 8px;
  height: 28px;
  padding: 0 10px;
  border: 0;
  border-radius: var(--r-sm);
  background: transparent;
  color: var(--text);
  font: inherit;
  text-align: left;
  cursor: pointer;
}
.mi:hover:not(:disabled),
.mi:focus-visible {
  background: var(--bg-hover);
}
.mi:disabled {
  color: var(--text-3);
  cursor: default;
}
.menu-pop-enter-active {
  transition: opacity 140ms var(--ease-out), transform 200ms var(--ease-spring);
}
.menu-pop-leave-active {
  transition: opacity 100ms var(--ease-out), transform 100ms var(--ease-out);
}
.menu-pop-enter-from {
  opacity: 0;
  transform: translateY(-4px) scale(0.96);
}
.menu-pop-leave-to {
  opacity: 0;
  transform: scale(0.98);
}
</style>
