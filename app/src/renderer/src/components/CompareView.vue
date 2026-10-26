<script setup lang="ts">
import { computed, ref } from 'vue'
import { ArrowLeftRight, Check, X } from '@lucide/vue'
import { useEventListener } from '@vueuse/core'
import type { BlobRef, VersionStatus } from '@/api/types'
import { useFocusTrap } from '@/composables/focusTrap'
import AssetViewer from './viewers/AssetViewer.vue'
import { viewsFor } from './viewers/detect'

export interface CompareItem {
  key: string
  name: string
  meta: string
  status: VersionStatus
  lead: BlobRef
  siblings: BlobRef[]
}

const props = defineProps<{ items: CompareItem[] | null }>()
const emit = defineEmits<{ close: []; review: [key: string, action: 'approve' | 'reject'] }>()

const STATUS_TEXT: Record<VersionStatus, string> = { draft: 'Nháp', approved: 'Đã duyệt', rejected: 'Từ chối' }

const swapped = ref(false)
const pair = computed(() => {
  const list = props.items?.slice(0, 2) ?? []
  return swapped.value ? [...list].reverse() : list
})
const views = computed(() => pair.value.map((it) => viewsFor(it.lead, it.siblings)[0]))

const root = ref<HTMLElement | null>(null)
const closeBtn = ref<HTMLButtonElement | null>(null)
const isOpen = computed(() => pair.value.length === 2)
const trap = useFocusTrap(root, isOpen, closeBtn)

useEventListener(
  document,
  'keydown',
  (e: KeyboardEvent) => {
    if (!isOpen.value) return
    if (trap.onTab(e)) return
    if (e.key !== 'Escape') return
    e.preventDefault()
    e.stopPropagation()
    emit('close')
  },
  { capture: true }
)
</script>

<template>
  <Teleport to="body">
    <Transition name="cmp">
      <div v-if="isOpen" ref="root" class="scrim" role="dialog" aria-modal="true" aria-label="So sánh 2 bản" @click.self="emit('close')">
        <section class="sheet">
          <header class="head">
            <h2 class="headline">So sánh 2 bản</h2>
            <span class="caption">Xem cạnh nhau rồi duyệt từng bản</span>
            <span class="grow" />
            <button type="button" class="btn btn-plain" title="Đổi chỗ trái / phải" @click="swapped = !swapped"><ArrowLeftRight /> Đổi chỗ</button>
            <button ref="closeBtn" type="button" class="btn btn-plain btn-icon" aria-label="Đóng (Esc)" title="Đóng (Esc)" @click="emit('close')"><X /></button>
          </header>
          <TransitionGroup name="cmp-col" tag="div" class="cols">
            <article v-for="(it, i) in pair" :key="it.key" class="col" :class="`vs-${it.status}`">
              <div class="stage">
                <AssetViewer v-if="views[i]" :view="views[i]!" compact />
              </div>
              <footer class="cap">
                <div class="who">
                  <span class="name" :title="it.name">{{ it.name }}</span>
                  <span class="caption meta">{{ it.meta }}</span>
                </div>
                <Transition name="fade" mode="out-in">
                  <span :key="it.status" class="pill">{{ STATUS_TEXT[it.status] }}</span>
                </Transition>
                <div class="review" role="group" :aria-label="`Duyệt ${it.name}`">
                  <button type="button" class="btn rv ok" :class="{ on: it.status === 'approved' }" :aria-pressed="it.status === 'approved'" @click="emit('review', it.key, 'approve')">
                    <Check /> Duyệt
                  </button>
                  <button type="button" class="btn rv no" :class="{ on: it.status === 'rejected' }" :aria-pressed="it.status === 'rejected'" @click="emit('review', it.key, 'reject')">
                    <X /> Từ chối
                  </button>
                </div>
              </footer>
            </article>
          </TransitionGroup>
        </section>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.scrim {
  position: fixed;
  inset: 0;
  z-index: 50;
  display: grid;
  place-items: center;
  padding: 32px 24px;
  background: rgba(0, 0, 0, 0.5);
  backdrop-filter: blur(6px);
}
.sheet {
  width: min(1480px, 100%);
  max-height: 100%;
  display: grid;
  grid-template-rows: auto minmax(0, 1fr);
  border-radius: var(--r-xl);
  overflow: hidden;
  background: var(--bg-elevated);
  box-shadow: var(--shadow-3);
}
.head {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 10px 10px 18px;
  border-bottom: 0.5px solid var(--separator);
}
.grow {
  flex: 1;
}
.cols {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0.5px;
  background: var(--separator);
}
.col {
  display: grid;
  grid-template-rows: minmax(0, 1fr) auto;
  background: var(--bg-elevated);
  min-width: 0;
}
.stage {
  height: min(68vh, 720px);
  min-height: 300px;
  background: var(--bg-sunken);
  transition: opacity var(--t-base);
}
.vs-rejected .stage {
  opacity: 0.6;
}
.cap {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 12px 10px 16px;
  border-top: 0.5px solid var(--separator);
}
.who {
  flex: 1;
  min-width: 0;
  display: grid;
}
.name {
  font-weight: 600;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.meta {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.pill {
  flex: none;
  font-size: var(--fs-caption);
  font-weight: 600;
  padding: 1px 7px;
  border-radius: 999px;
  color: var(--text-2);
  background: var(--bg-active);
}
.vs-approved .pill {
  color: var(--st-ok);
  background: var(--st-ok-soft);
}
.vs-rejected .pill {
  color: var(--st-fail);
  background: var(--st-fail-soft);
}
.vs-draft .pill {
  color: var(--accent);
  background: var(--accent-soft);
}
.review {
  display: flex;
  gap: 6px;
}
.rv {
  height: 28px;
  padding: 0 10px;
  font-size: var(--fs-callout);
}
.rv.ok.on {
  color: var(--st-ok);
  background: var(--st-ok-soft);
  box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--st-ok) 40%, transparent);
}
.rv.no {
  color: var(--st-fail);
}
.rv.no.on {
  background: var(--st-fail-soft);
  box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--st-fail) 40%, transparent);
}
.cmp-enter-active {
  transition: opacity var(--t-base) var(--ease-out);
}
.cmp-enter-active .sheet {
  transition: transform var(--t-slow) var(--ease-spring);
}
.cmp-leave-active {
  transition: opacity var(--t-fast) var(--ease-in);
}
.cmp-enter-from,
.cmp-leave-to {
  opacity: 0;
}
.cmp-enter-from .sheet {
  transform: scale(0.96);
}
.cmp-col-move {
  transition: transform var(--t-slow) var(--ease-spring);
}
.fade-enter-active,
.fade-leave-active {
  transition: opacity var(--t-fast);
}
.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}
@media (prefers-reduced-motion: reduce) {
  .cmp-enter-active .sheet,
  .cmp-col-move {
    transition: none;
  }
  .cmp-enter-from .sheet {
    transform: none;
  }
}
</style>
