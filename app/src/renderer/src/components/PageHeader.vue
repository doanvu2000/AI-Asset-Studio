<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'

defineProps<{ title: string; subtitle?: string }>()

const root = ref<HTMLElement>()
const actions = ref<HTMLElement>()
let observer: ResizeObserver | null = null

// Buttons wrap their label in `.lbl` (plus aria-label + title); when the actions
// plus a readable title no longer fit, the labels drop and only icons remain.
const TITLE_MIN = 200

function fit(): void {
  const h = root.value
  const a = actions.value
  if (!h || !a) return
  h.classList.remove('compact')
  const cs = getComputedStyle(h)
  const room = h.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight) - parseFloat(cs.columnGap)
  if (a.offsetWidth + TITLE_MIN > room) h.classList.add('compact')
}

onMounted(() => {
  observer = new ResizeObserver(fit)
  if (root.value) observer.observe(root.value)
  if (actions.value) observer.observe(actions.value)
  fit()
})
onBeforeUnmount(() => observer?.disconnect())
</script>

<template>
  <header ref="root" class="page-header">
    <div class="titles">
      <slot name="leading" />
      <div class="t">
        <h1 class="title" :title="title">{{ title }}</h1>
        <p v-if="subtitle || $slots.subtitle" class="caption sub" :title="subtitle"><slot name="subtitle">{{ subtitle }}</slot></p>
      </div>
    </div>
    <div ref="actions" class="actions"><slot /></div>
  </header>
</template>

<style scoped>
.page-header {
  -webkit-app-region: drag;
  flex: none;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--sp-4);
  min-height: var(--toolbar-h);
  padding: 6px calc(var(--caption-w) + var(--sp-2)) 6px var(--sp-6);
  border-bottom: 1px solid var(--hair-soft);
  background: color-mix(in srgb, var(--bg-content) 85%, transparent);
  backdrop-filter: saturate(1.6) blur(20px);
  position: relative;
  z-index: 5;
}
.titles {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  min-width: 0;
}
.t {
  min-width: 0;
}
.t h1 {
  font-size: 24px;
  font-weight: 700;
  line-height: 1.2;
  letter-spacing: -0.02em;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}
.sub {
  margin-top: 1px;
  overflow-wrap: anywhere;
}
.actions,
.titles :deep(button),
.titles :deep(a) {
  -webkit-app-region: no-drag;
}
.actions {
  flex: none;
  display: flex;
  align-items: center;
  gap: var(--sp-2);
}
.compact .actions :deep(.lbl) {
  display: none;
}
</style>
