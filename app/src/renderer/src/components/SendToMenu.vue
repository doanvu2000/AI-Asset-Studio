<script setup lang="ts">
import { computed, nextTick, ref, useId } from 'vue'
import { useRouter } from 'vue-router'
import { onClickOutside, useEventListener } from '@vueuse/core'
import { ChevronDown, Send } from '@lucide/vue'
import { useFlows } from '@/stores/flows'
import { useProjects } from '@/stores/projects'
import { useRecipes } from '@/stores/recipes'
import { recordRecent, sendItems, type SendItem, type SendSource } from '@/composables/sendTo'
import PackDialog from './PackDialog.vue'
import LibraryImportDialog from './LibraryImportDialog.vue'

const props = defineProps<{ source: SendSource; compact?: boolean; extra?: string }>()

const router = useRouter()
const recipes = useRecipes()
const projects = useProjects()
const flows = useFlows()
const uid = useId()
const open = ref(false)
const packOpen = ref(false)
const importOpen = ref(false)
const active = ref(0)
const button = ref<HTMLButtonElement | null>(null)
const menu = ref<HTMLElement | null>(null)
const pos = ref<{ left: number; top?: number; bottom?: number; maxHeight: number }>({ left: 0, top: 0, maxHeight: 420 })

const items = computed<SendItem[]>(() => sendItems(props.source, recipes.list, projects.byId(props.source.projectId), flows.defs))
const itemId = (i: number): string => `${uid}-st-${i}`

// The import/pack dialogs hand focus back to the trigger while the confirming Enter/click is still being
// dispatched, which used to reopen the menu right after the dialog closed.
let quietUntil = 0
function dialogClosed(): void {
  quietUntil = performance.now() + 300
  void nextTick(() => button.value?.focus())
}

async function show(): Promise<void> {
  if (performance.now() < quietUntil) return
  const r = button.value?.getBoundingClientRect()
  if (!r) return
  const left = Math.max(8, Math.min(r.left, window.innerWidth - 308))
  const below = window.innerHeight - r.bottom - 12
  // The Gallery selection bar sits at the bottom edge, so open upwards when that side has more room.
  pos.value = below < 240 && r.top - 12 > below ? { left, bottom: window.innerHeight - r.top + 4, maxHeight: Math.min(420, r.top - 12) } : { left, top: r.bottom + 4, maxHeight: Math.min(420, below) }
  active.value = 0
  open.value = true
  void recipes.load()
  if (!flows.defsLoaded) void flows.loadDefs()
  await nextTick()
  menu.value?.focus()
}

function close(refocus = true): void {
  if (!open.value) return
  open.value = false
  if (refocus) button.value?.focus()
}

function choose(i: number): void {
  const it = items.value[i]
  if (!it) return
  close(false)
  if (it.target === 'pack') packOpen.value = true
  else if (it.target === 'import') importOpen.value = true
  else if (it.to) {
    recordRecent(it, sourceName())
    void router.push(it.to)
  }
}

function sourceName(): string | undefined {
  const s = props.source
  const name = s.blobs?.[0]?.name || s.importBlobs?.[0]?.name
  if (name) return name
  return s.folder?.split(/[\\/]/).filter(Boolean).pop() ?? projects.byId(s.projectId)?.name
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

defineExpose({ show })

onClickOutside(menu, () => close(false), { ignore: [button] })
useEventListener(window, 'resize', () => close(false))
useEventListener(window, 'scroll', () => close(false), { capture: true, passive: true })
</script>

<template>
  <button
    ref="button"
    type="button"
    class="btn btn-plain"
    :class="[extra, { 'btn-icon': compact }]"
    :disabled="!items.length"
    aria-haspopup="menu"
    :aria-expanded="open"
    :aria-label="compact ? 'Gửi tới' : undefined"
    :title="items.length ? 'Chuyển kết quả sang màn khác, đã điền sẵn' : 'Chưa có màn nào nhận được kết quả này'"
    @click="open ? close() : show()"
  >
    <Send />
    <span v-if="!compact">Gửi tới…</span>
    <ChevronDown v-if="!compact" :size="13" class="chev" :class="{ up: open }" aria-hidden="true" />
  </button>
  <Teleport to="body">
    <Transition name="menu">
      <div
        v-if="open"
        ref="menu"
        class="s-menu"
        role="menu"
        tabindex="-1"
        aria-label="Gửi tới"
        :aria-activedescendant="items.length ? itemId(active) : undefined"
        :style="{ left: `${pos.left}px`, top: pos.top === undefined ? undefined : `${pos.top}px`, bottom: pos.bottom === undefined ? undefined : `${pos.bottom}px`, maxHeight: `${pos.maxHeight}px` }"
        @keydown="onKey"
      >
        <p class="s-head caption">Gửi tới</p>
        <template v-for="(it, i) in items" :key="it.id">
          <hr v-if="i > 0 && it.target !== 'recipe' && it.target !== 'flow' && ['recipe', 'flow'].includes(items[i - 1]?.target ?? '')" class="s-sep" />
          <div :id="itemId(i)" role="menuitem" class="s-item" :class="{ active: i === active }" @mousemove="active = i" @click="choose(i)">
            <span class="s-label">{{ it.label }}</span>
            <span v-if="it.hint" class="s-hint">{{ it.hint }}</span>
          </div>
        </template>
      </div>
    </Transition>
  </Teleport>
  <LibraryImportDialog v-if="source.importPaths?.length || source.importBlobs?.length" :open="importOpen" :paths="source.importPaths" :blobs="source.importBlobs" :project-id="source.projectId" @close="importOpen = false; dialogClosed()" />
  <PackDialog v-if="source.projectId && source.stepIds?.length" :open="packOpen" :project-id="source.projectId" :preselect="source.stepIds" @close="packOpen = false; dialogClosed()" />
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
.s-menu {
  position: fixed;
  z-index: 60;
  width: 300px;
  overflow-y: auto;
  padding: 5px;
  border-radius: var(--r-md);
  background: var(--bg-elevated);
  box-shadow: var(--shadow-2), var(--hairline);
  font-size: var(--fs-body);
  color: var(--text);
  outline: none;
  transform-origin: bottom left;
}
.s-head {
  margin: 2px 8px 4px;
  color: var(--text-3);
}
.s-sep {
  margin: 4px 6px;
  border: 0;
  border-top: 1px solid var(--separator);
}
.s-item {
  display: flex;
  align-items: center;
  gap: 8px;
  min-height: 28px;
  padding: 3px 10px;
  border-radius: var(--r-sm);
  cursor: default;
}
.s-item.active {
  background: var(--accent);
  color: var(--text-on-accent);
}
.s-label {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.s-hint {
  max-width: 50%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: var(--fs-caption);
  color: var(--text-3);
}
.s-item.active .s-hint {
  color: inherit;
  opacity: 0.85;
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
  .menu-enter-active,
  .menu-leave-active {
    transition: none;
  }
}
</style>
