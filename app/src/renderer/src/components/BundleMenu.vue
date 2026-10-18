<script setup lang="ts">
import { computed, nextTick, ref, useId, watch } from 'vue'
import { onClickOutside, useEventListener } from '@vueuse/core'
import { ChevronDown, FolderDown, FolderOpen, LoaderCircle } from '@lucide/vue'
import type { BundleOptions } from '../../../preload/types'
import { useAssetFile } from '@/composables/assetFile'
import { usePresets } from '@/stores/catalog'

const props = defineProps<{ stepId: string; name?: string; compact?: boolean }>()

const file = useAssetFile()
const presets = usePresets()
const uid = useId()
const opts = ref<BundleOptions | null>(null)
const open = ref(false)
const active = ref(0)
const button = ref<HTMLButtonElement | null>(null)
const menu = ref<HTMLElement | null>(null)
const pos = ref({ left: 0, top: 0 })

watch(
  () => props.stepId,
  async (id) => {
    opts.value = null
    if (!window.studio?.asset) return
    try {
      const o = await window.studio.asset.bundleOptions(id)
      if (id === props.stepId) opts.value = o
    } catch {
      /* not exportable as a folder (mock, unfinished step) → no button */
    }
    if (opts.value?.sprite_sheet) void presets.load()
  },
  { immediate: true }
)

const visible = computed(() => !!opts.value && (opts.value.sprite_sheet || opts.value.files > 1))
type Item = { id: string; label: string; hint?: string; preset?: string; reveal?: boolean }
const items = computed<Item[]>(() => {
  const o = opts.value
  if (!o?.sprite_sheet) return []
  const list: Item[] = presets.list.map((p) => ({
    id: `p-${p.id}`,
    label: p.name,
    hint: p.id === o.preset ? 'Đang dùng' : undefined,
    preset: p.id
  }))
  return [...list, { id: 'reveal', label: 'Mở thư mục đã xuất', preset: lastPreset.value, reveal: true }]
})
const lastPreset = ref<string | undefined>(undefined)
const itemId = (i: number): string => `${uid}-it-${i}`

async function show(): Promise<void> {
  const r = button.value?.getBoundingClientRect()
  if (!r) return
  pos.value = { left: Math.min(r.left, window.innerWidth - 248), top: r.bottom + 4 }
  active.value = 0
  open.value = true
  await nextTick()
  menu.value?.focus()
}

function close(refocus = true): void {
  if (!open.value) return
  open.value = false
  if (refocus) button.value?.focus()
}

function onButton(): void {
  if (!opts.value?.sprite_sheet) return void file.saveBundle(props.stepId, undefined, props.name)
  if (open.value) close()
  else void show()
}

function choose(i: number): void {
  const it = items.value[i]
  if (!it) return
  close()
  if (it.reveal) return void file.revealBundle(props.stepId, it.preset ?? opts.value?.preset ?? 'studio', props.name)
  lastPreset.value = it.preset
  void file.saveBundle(props.stepId, it.preset, props.name)
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
  if (!fn || !n) return
  e.preventDefault()
  e.stopPropagation()
  fn()
}

onClickOutside(menu, () => close(false), { ignore: [button] })
useEventListener(window, 'resize', () => close(false))
useEventListener(window, 'scroll', () => close(false), { capture: true, passive: true })
</script>

<template>
  <template v-if="visible">
    <button
      ref="button"
      type="button"
      class="btn btn-plain"
      :class="{ 'btn-icon': compact }"
      :disabled="!!file.busy.value"
      :aria-haspopup="opts?.sprite_sheet ? 'menu' : undefined"
      :aria-expanded="opts?.sprite_sheet ? open : undefined"
      :aria-label="compact ? 'Xuất thư mục' : undefined"
      :title="opts?.sprite_sheet ? 'Sprite sheet + từng khung, xếp theo engine' : `Lưu cả ${opts?.files} tệp vào một thư mục`"
      @click="onButton"
    >
      <LoaderCircle v-if="file.busy.value === 'bundle'" class="spin" />
      <FolderDown v-else />
      <span v-if="!compact">{{ opts?.sprite_sheet ? 'Xuất cho engine' : 'Xuất thư mục…' }}</span>
      <ChevronDown v-if="opts?.sprite_sheet && !compact" :size="13" class="chev" :class="{ up: open }" aria-hidden="true" />
    </button>
    <Teleport to="body">
      <Transition name="menu">
        <div
          v-if="open"
          ref="menu"
          class="b-menu"
          role="menu"
          tabindex="-1"
          aria-label="Xuất sprite sheet cho engine"
          :aria-activedescendant="itemId(active)"
          :style="{ left: `${pos.left}px`, top: `${pos.top}px` }"
          @keydown="onKey"
        >
          <p class="b-head caption">Sheet + từng khung, thứ tự hướng theo engine</p>
          <template v-for="(it, i) in items" :key="it.id">
            <hr v-if="it.reveal" class="b-sep" />
            <div
              :id="itemId(i)"
              role="menuitem"
              class="b-item"
              :class="{ active: i === active }"
              @mousemove="active = i"
              @click="choose(i)"
            >
              <FolderOpen v-if="it.reveal" :size="14" aria-hidden="true" />
              <span class="b-label">{{ it.reveal ? it.label : `${it.label}…` }}</span>
              <span v-if="it.hint" class="b-hint">{{ it.hint }}</span>
            </div>
          </template>
        </div>
      </Transition>
    </Teleport>
  </template>
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
.b-menu {
  position: fixed;
  z-index: 60;
  width: 240px;
  padding: 5px;
  border-radius: var(--r-md);
  background: var(--bg-elevated);
  box-shadow: var(--shadow-2), var(--hairline);
  font-size: var(--fs-body);
  color: var(--text);
  outline: none;
  transform-origin: top left;
}
.b-head {
  margin: 2px 8px 4px;
  color: var(--text-3);
}
.b-sep {
  margin: 4px 6px;
  border: 0;
  border-top: 1px solid var(--separator);
}
.b-item {
  display: flex;
  align-items: center;
  gap: 8px;
  min-height: 28px;
  padding: 3px 10px;
  border-radius: var(--r-sm);
  cursor: default;
}
.b-item.active {
  background: var(--accent);
  color: var(--text-on-accent);
}
.b-label {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.b-hint {
  font-size: var(--fs-caption);
  color: var(--text-3);
}
.b-item.active .b-hint {
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
  .menu-enter-active,
  .menu-leave-active {
    transition: none;
  }
}
</style>
