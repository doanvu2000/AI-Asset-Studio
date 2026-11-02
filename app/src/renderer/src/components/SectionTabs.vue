<script setup lang="ts">
import { useRouter } from 'vue-router'

defineProps<{ tabs: { to: string; label: string }[]; current: string; label: string }>()
const router = useRouter()
</script>

<template>
  <div class="tabs">
    <div class="seg" role="tablist" :aria-label="label">
      <button v-for="t in tabs" :key="t.to" role="tab" :aria-selected="current === t.to" :class="{ on: current === t.to }" @click="current !== t.to && router.push(t.to)">
        {{ t.label }}
      </button>
    </div>
  </div>
</template>

<style scoped>
.tabs {
  flex: none;
  padding: 10px 24px 0;
}
</style>
