<script lang="ts">
export interface SelectOption {
  value: string
  label: string
}
</script>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, useId } from 'vue'
import { onClickOutside, useEventListener } from '@vueuse/core'
import { Check, ChevronsUpDown } from '@lucide/vue'

defineOptions({ inheritAttrs: false })
const props = defineProps<{ modelValue: string; options: SelectOption[]; id?: string; ariaLabel?: string }>()
const emit = defineEmits<{ 'update:modelValue': [value: string] }>()

const uid = useId()
const open = ref(false)
const active = ref(0)
const button = ref<HTMLButtonElement | null>(null)
const list = ref<HTMLElement | null>(null)
const pos = ref({ left: 0, top: 0, width: 0, up: false })

const current = computed(() => props.options.find((o) => o.value === props.modelValue))
const optId = (i: number): string => `${uid}-opt-${i}`

function place(): void {
  const r = button.value?.getBoundingClientRect()
  if (!r) return
  const estimate = Math.min(280, props.options.length * 30 + 10)
  const up = r.bottom + estimate + 8 > window.innerHeight && r.top > estimate + 8
  pos.value = { left: r.left, top: up ? r.top - 4 : r.bottom + 4, width: Math.max(r.width, 160), up }
}

async function show(): Promise<void> {
  if (!props.options.length) return
  place()
  active.value = Math.max(0, props.options.findIndex((o) => o.value === props.modelValue))
  open.value = true
  await nextTick()
  list.value?.focus()
  list.value?.querySelector<HTMLElement>(`#${CSS.escape(optId(active.value))}`)?.scrollIntoView({ block: 'nearest' })
}

function close(refocus = true): void {
  if (!open.value) return
  open.value = false
  if (refocus) button.value?.focus()
}

function choose(i: number): void {
  const o = props.options[i]
  if (o && o.value !== props.modelValue) emit('update:modelValue', o.value)
  close()
}

function move(to: number): void {
  const n = props.options.length
  if (!n) return
  active.value = (to + n) % n
  void nextTick(() => list.value?.querySelector<HTMLElement>(`#${CSS.escape(optId(active.value))}`)?.scrollIntoView({ block: 'nearest' }))
}

function onButtonKey(e: KeyboardEvent): void {
  if (['ArrowDown', 'ArrowUp', 'Enter', ' '].includes(e.key)) {
    e.preventDefault()
    void show()
  }
}

function onListKey(e: KeyboardEvent): void {
  const keys: Record<string, () => void> = {
    ArrowDown: () => move(active.value + 1),
    ArrowUp: () => move(active.value - 1),
    Home: () => move(0),
    End: () => move(props.options.length - 1),
    Enter: () => choose(active.value),
    ' ': () => choose(active.value),
    Escape: () => close()
  }
  if (e.key === 'Tab') return close(false)
  const fn = keys[e.key]
  if (!fn) return
  e.preventDefault()
  e.stopPropagation()
  fn()
}

onClickOutside(list, () => close(false), { ignore: [button] })
// The list is fixed-positioned in <body>, so any scroll or resize would detach it from its button.
useEventListener(window, 'resize', () => close(false))
useEventListener(window, 'scroll', (e: Event) => !list.value?.contains(e.target as globalThis.Node) && close(false), { capture: true, passive: true })
onBeforeUnmount(() => (open.value = false))
</script>

<template>
  <button
    v-bind="$attrs"
    :id="id"
    ref="button"
    type="button"
    class="field sel"
    :class="{ open }"
    aria-haspopup="listbox"
    :aria-expanded="open"
    :aria-label="ariaLabel"
    @click="open ? close() : show()"
    @keydown="onButtonKey"
  >
    <span class="sel-label">{{ current?.label ?? modelValue }}</span>
    <ChevronsUpDown :size="13" class="sel-ic" aria-hidden="true" />
  </button>
  <Teleport to="body">
    <Transition name="menu">
      <ul
        v-if="open"
        ref="list"
        class="sel-menu"
        :class="{ up: pos.up }"
        role="listbox"
        tabindex="-1"
        :aria-label="ariaLabel"
        :aria-activedescendant="optId(active)"
        :style="{ left: `${pos.left}px`, top: `${pos.top}px`, minWidth: `${pos.width}px` }"
        @keydown="onListKey"
      >
        <li
          v-for="(o, i) in options"
          :id="optId(i)"
          :key="o.value"
          role="option"
          class="sel-opt"
          :class="{ active: i === active }"
          :aria-selected="o.value === modelValue"
          @mousemove="active = i"
          @click="choose(i)"
        >
          <Check v-if="o.value === modelValue" :size="13" class="tick" aria-hidden="true" />
          <span>{{ o.label }}</span>
        </li>
      </ul>
    </Transition>
  </Teleport>
</template>

<style scoped>
.sel {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
  min-height: 26px;
  padding: 5px 9px;
  font-size: var(--fs-body);
  text-align: left;
  cursor: default;
  color: var(--text);
}
.sel.open {
  box-shadow: 0 0 0 3px var(--focus-ring), var(--hairline);
}
.sel-label {
  flex: 1;
  min-width: 0;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.sel-ic {
  flex: none;
  color: var(--text-3);
}
.sel-menu {
  position: fixed;
  z-index: 60;
  max-height: 280px;
  overflow-y: auto;
  margin: 0;
  padding: 5px;
  list-style: none;
  border-radius: var(--r-md);
  background: var(--bg-elevated);
  box-shadow: var(--shadow-2), var(--hairline);
  font-size: var(--fs-body);
  color: var(--text);
  outline: none;
  transform-origin: top left;
}
.sel-menu.up {
  transform: translateY(-100%);
  transform-origin: bottom left;
}
.sel-opt {
  position: relative;
  display: flex;
  align-items: center;
  min-height: 26px;
  padding: 3px 10px 3px 26px;
  border-radius: var(--r-sm);
  white-space: nowrap;
  cursor: default;
}
.sel-opt.active {
  background: var(--accent);
  color: var(--text-on-accent);
}
.tick {
  position: absolute;
  left: 8px;
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
</style>
