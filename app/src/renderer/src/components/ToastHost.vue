<script setup lang="ts">
import { CircleAlert, CircleCheck, Info } from '@lucide/vue'
import { useRouter } from 'vue-router'
import { useToasts, type Toast } from '@/stores/toasts'

const toasts = useToasts()
const router = useRouter()
function run(t: Toast): void {
  if (t.action) void router.push(t.action.to)
}
const icons = { info: Info, success: CircleCheck, error: CircleAlert }
</script>

<template>
  <div class="host" aria-live="polite">
    <TransitionGroup name="toast">
      <div v-for="t in toasts.items" :key="t.id" class="toast" :class="t.kind" @click="toasts.dismiss(t.id)">
        <component :is="icons[t.kind]" :size="16" />
        <span>{{ t.text }}</span>
        <button v-if="t.action" type="button" class="act" @click="run(t)">{{ t.action.label }}</button>
      </div>
    </TransitionGroup>
  </div>
</template>

<style scoped>
.host {
  position: fixed;
  right: 20px;
  bottom: 20px;
  display: grid;
  gap: 8px;
  z-index: 100;
  max-width: 380px;
}
.toast {
  display: flex;
  gap: 10px;
  align-items: flex-start;
  padding: 12px 16px;
  border-radius: var(--r-lg);
  background: color-mix(in srgb, var(--bg-elevated) 90%, transparent);
  backdrop-filter: blur(24px) saturate(1.6);
  box-shadow: var(--shadow-3), var(--hairline);
  font-size: var(--fs-callout);
  cursor: default;
}
.success svg {
  color: var(--st-ok);
}
.error svg {
  color: var(--st-fail);
}
.info svg {
  color: var(--accent);
}
svg {
  flex: none;
  margin-top: 1px;
}
.act {
  flex: none;
  margin-left: 4px;
  padding: 0;
  border: 0;
  background: none;
  color: var(--accent);
  font: inherit;
  font-weight: 600;
  cursor: pointer;
}
.act:hover {
  text-decoration: underline;
}
.toast-enter-active {
  transition:
    transform var(--t-slow) var(--ease-spring),
    opacity var(--t-base);
}
.toast-leave-active {
  transition:
    transform var(--t-fast) var(--ease-in),
    opacity var(--t-fast);
  position: absolute;
}
.toast-move {
  transition: transform var(--t-base) var(--ease-spring);
}
.toast-enter-from {
  transform: translateY(12px) scale(0.96);
  opacity: 0;
}
.toast-leave-to {
  transform: translateX(16px);
  opacity: 0;
}
</style>
