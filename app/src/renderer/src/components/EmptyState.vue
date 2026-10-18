<script setup lang="ts">
import type { Component } from 'vue'

withDefaults(defineProps<{ icon?: Component; art?: string; artSize?: number; title: string; text?: string }>(), {
  artSize: 152
})
</script>

<template>
  <div class="empty">
    <img v-if="art" class="art" :src="art" alt="" :width="artSize" :height="artSize" decoding="async" draggable="false" />
    <span v-else-if="icon" class="ic"><component :is="icon" :size="26" :stroke-width="1.6" /></span>
    <p class="headline">{{ title }}</p>
    <p v-if="text" class="secondary txt">{{ text }}</p>
    <div v-if="$slots.default" class="act"><slot /></div>
  </div>
</template>

<style scoped>
.empty {
  display: grid;
  justify-items: center;
  gap: 6px;
  padding: 48px 24px;
  text-align: center;
  animation: fade-up var(--t-slow) var(--ease-out);
}
.art {
  margin-bottom: 4px;
  object-fit: contain;
  user-select: none;
  animation: art-in 520ms var(--ease-spring) both;
}
.ic {
  display: grid;
  place-items: center;
  width: 52px;
  height: 52px;
  margin-bottom: 6px;
  border-radius: 16px;
  color: var(--accent);
  background: var(--accent-soft);
}
.txt {
  max-width: 44ch;
}
.act {
  margin-top: 10px;
}
@keyframes art-in {
  from {
    opacity: 0;
    transform: translateY(8px) scale(0.94);
  }
}
@media (prefers-reduced-motion: reduce) {
  .art {
    animation: none;
  }
}
</style>
