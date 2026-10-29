<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useEventListener } from '@vueuse/core'
import { ChevronRight, Sparkles, X } from '@lucide/vue'
import { APP_VERSION, CHANGELOG, markSeen, seenVersion, shouldShowWhatsNew, whatsNewOpen } from '@/composables/changelog'
import { useFocusTrap } from '@/composables/focusTrap'
import { useConnection } from '@/stores/connection'

const conn = useConnection()
const root = ref<HTMLElement | null>(null)
const currentBtn = ref<HTMLElement | null>(null)
const expanded = ref(new Set<string>())
const trap = useFocusTrap(root, whatsNewOpen, currentBtn)

const allOpen = computed(() => expanded.value.size === CHANGELOG.length)
const dateOf = (iso: string): string => {
  const d = new Date(`${iso}T00:00:00`)
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleDateString('vi-VN', { day: 'numeric', month: 'numeric', year: 'numeric' })
}

function bindCurrent(version: string, el: unknown): void {
  if (version === APP_VERSION) currentBtn.value = el instanceof HTMLElement ? el : null
}

function toggle(version: string): void {
  const next = new Set(expanded.value)
  if (!next.delete(version)) next.add(version)
  expanded.value = next
}

function toggleAll(): void {
  expanded.value = allOpen.value ? new Set() : new Set(CHANGELOG.map((r) => r.version))
}

function close(): void {
  whatsNewOpen.value = false
}

watch(whatsNewOpen, (open) => {
  if (open) {
    const current = CHANGELOG.some((r) => r.version === APP_VERSION) ? APP_VERSION : CHANGELOG[0]?.version
    expanded.value = new Set(current ? [current] : [])
  } else markSeen()
})

useEventListener(
  document,
  'keydown',
  (e: KeyboardEvent) => {
    if (!whatsNewOpen.value) return
    if (trap.onTab(e)) return
    if (e.key !== 'Escape') return
    e.preventDefault()
    e.stopPropagation()
    close()
  },
  { capture: true }
)

// Waits for the core so a fresh install sees the app (or the offline screen) settle first, and never stacks on another dialog.
let offered = false
let timer = 0
function offer(): void {
  window.clearTimeout(timer)
  if (offered || !shouldShowWhatsNew(seenVersion(), APP_VERSION)) return
  if (!conn.core.mock && !conn.transport) return
  timer = window.setTimeout(() => {
    if (offered || whatsNewOpen.value || document.querySelector('[role=dialog],[role=alertdialog]')) return
    offered = true
    whatsNewOpen.value = true
  }, 1200)
}
watch(() => [conn.transport, conn.core.mock], offer, { immediate: true })
onBeforeUnmount(() => window.clearTimeout(timer))
</script>

<template>
  <Teleport to="body">
    <Transition name="wn">
      <div v-if="whatsNewOpen" class="scrim" @mousedown.self="close">
        <div ref="root" class="sheet" role="dialog" aria-modal="true" aria-labelledby="wn-title" data-whats-new>
          <header class="head">
            <Sparkles :size="16" class="h-ic" aria-hidden="true" />
            <div class="h-text">
              <h2 id="wn-title" class="title-2">Có gì mới</h2>
              <p class="caption">AI Asset Studio {{ APP_VERSION }}</p>
            </div>
            <button type="button" class="btn btn-plain" :aria-pressed="allOpen" @click="toggleAll">{{ allOpen ? 'Thu hết' : 'Mở hết' }}</button>
            <button type="button" class="btn btn-plain btn-icon" title="Đóng" aria-label="Đóng" @click="close"><X :size="16" aria-hidden="true" /></button>
          </header>

          <div class="body">
            <ul class="rel-list">
              <li v-for="r in CHANGELOG" :key="r.version" class="rel">
                <button
                  :id="`wn-h-${r.version}`"
                  :ref="(el) => bindCurrent(r.version, el)"
                  type="button"
                  class="rel-head"
                  :aria-expanded="expanded.has(r.version)"
                  :aria-controls="`wn-p-${r.version}`"
                  @click="toggle(r.version)"
                >
                  <ChevronRight :size="14" class="chev" :class="{ open: expanded.has(r.version) }" aria-hidden="true" />
                  <span class="ver">{{ r.version }}</span>
                  <span v-if="r.version === APP_VERSION" class="pill">Đang dùng</span>
                  <span class="date caption">{{ dateOf(r.date) }}</span>
                  <span class="count caption">{{ r.items.length }} thay đổi</span>
                </button>
                <div
                  :id="`wn-p-${r.version}`"
                  role="region"
                  :aria-labelledby="`wn-h-${r.version}`"
                  class="panel"
                  :class="{ open: expanded.has(r.version) }"
                  :inert="!expanded.has(r.version)"
                >
                  <div class="panel-in">
                    <ul class="items">
                      <li v-for="(item, i) in r.items" :key="i">{{ item }}</li>
                    </ul>
                  </div>
                </div>
              </li>
            </ul>
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.scrim {
  position: fixed;
  inset: 0;
  z-index: 1000;
  display: flex;
  justify-content: center;
  align-items: flex-start;
  padding: 8vh 16px 16px;
  background: var(--scrim);
}
.sheet {
  width: min(580px, 100%);
  max-height: 84vh;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  border-radius: var(--r-xl);
  background: var(--bg-elevated);
  box-shadow: var(--shadow-3), var(--hairline);
}
.head {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 12px 12px 12px 18px;
  border-bottom: 0.5px solid var(--separator);
}
.h-ic {
  flex: none;
  color: var(--accent);
}
.h-text {
  flex: 1;
  min-width: 0;
}
.h-text > * {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.body {
  flex: 1;
  min-height: 0;
  overflow: auto;
  padding: 10px 12px 14px;
}
.rel-list {
  list-style: none;
  margin: 0;
  padding: 0;
}
.rel + .rel {
  border-top: 0.5px solid var(--separator);
}
.rel-head {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  min-height: 40px;
  padding: 0 8px;
  border: 0;
  border-radius: var(--r-sm);
  background: transparent;
  text-align: left;
  cursor: pointer;
  transition: background var(--t-fast) var(--ease-out);
}
.rel-head:hover {
  background: var(--bg-hover);
}
.chev {
  flex: none;
  color: var(--text-3);
  transition: transform var(--t-base) var(--ease-out);
}
.chev.open {
  transform: rotate(90deg);
}
.ver {
  flex: none;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
}
.pill {
  flex: none;
  padding: 1px 7px;
  border-radius: 999px;
  background: var(--accent-soft);
  color: var(--accent);
  font-size: 11px;
  font-weight: 600;
}
.date {
  flex: 1;
  min-width: 0;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.count {
  flex: none;
  font-variant-numeric: tabular-nums;
}
.panel {
  display: grid;
  grid-template-rows: 0fr;
  transition: grid-template-rows var(--t-base) var(--ease-out);
}
.panel.open {
  grid-template-rows: 1fr;
}
.panel-in {
  min-height: 0;
  overflow: hidden;
}
.items {
  margin: 0;
  padding: 2px 10px 12px 38px;
  display: grid;
  gap: 4px;
  color: var(--text-2);
  overflow-wrap: anywhere;
}
.items li::marker {
  color: var(--text-3);
}
@media (prefers-reduced-motion: reduce) {
  .chev,
  .panel {
    transition: none;
  }
}
.wn-enter-active {
  transition: opacity var(--t-fast) var(--ease-out);
}
.wn-enter-active .sheet {
  transition: transform var(--t-base) var(--ease-spring);
}
.wn-leave-active {
  transition: opacity 120ms var(--ease-in);
}
.wn-enter-from,
.wn-leave-to {
  opacity: 0;
}
.wn-enter-from .sheet {
  transform: translateY(-8px) scale(0.98);
}
</style>
