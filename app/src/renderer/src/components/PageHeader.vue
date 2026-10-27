<script setup lang="ts">
defineProps<{ title: string; subtitle?: string }>()
</script>

<template>
  <header class="page-header">
    <div class="titles">
      <slot name="leading" />
      <div class="t">
        <h1 class="title">{{ title }}</h1>
        <p v-if="subtitle || $slots.subtitle" class="caption sub"><slot name="subtitle">{{ subtitle }}</slot></p>
      </div>
    </div>
    <div class="actions"><slot /></div>
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
  height: var(--toolbar-h);
  padding: 0 calc(var(--caption-w) + var(--sp-2)) 0 var(--sp-6);
  border-bottom: 0.5px solid var(--separator);
  background: color-mix(in srgb, var(--bg-content) 82%, transparent);
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
  font-size: 15px;
  letter-spacing: -0.012em;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.sub {
  margin-top: -1px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.actions,
.titles :deep(button),
.titles :deep(a) {
  -webkit-app-region: no-drag;
}
.actions {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
}
</style>
