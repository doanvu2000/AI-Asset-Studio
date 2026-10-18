<script setup lang="ts">
import type { Component } from 'vue'

defineProps<{ icon: Component; title: string; text?: string }>()
</script>

<template>
  <div class="empty">
    <span class="ic"><component :is="icon" :size="26" :stroke-width="1.6" /></span>
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
</style>
