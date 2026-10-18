<script setup lang="ts">
import { computed } from 'vue'
import { ChevronRight } from '@lucide/vue'
import type { RunRow, StepRow } from '@/api/types'
import { RUN_ACTIVE } from '@/api/types'
import { humanDuration, kindName } from '@/composables/format'
import { useRecipes } from '@/stores/recipes'
import RunStatusBadge from './RunStatusBadge.vue'
import ElapsedTimer from './ElapsedTimer.vue'
import RelTime from './RelTime.vue'

const props = defineProps<{ run: RunRow; steps?: StepRow[] }>()
const recipes = useRecipes()

const recipeName = computed(() => recipes.byId(props.run.recipe_id)?.name ?? props.run.recipe_id)
const active = computed(() => RUN_ACTIVE.has(props.run.status))
const current = computed(() =>
  props.steps?.find((s) => ['running', 'waiting_resource', 'awaiting_human', 'failed'].includes(s.status))
)
</script>

<template>
  <RouterLink :to="`/runs/${run.id}`" class="row">
    <div class="main">
      <div class="l1">
        <span class="title-text">{{ run.title || recipeName }}</span>
        <RunStatusBadge :status="run.status" />
      </div>
      <div class="l2 caption">
        <span>{{ recipeName }}</span>
        <span>·</span>
        <RelTime :ts="run.created_at" />
        <template v-if="current">
          <span>·</span>
          <span class="cur" :class="`c-${current.status}`">{{ kindName(current.kind) }}</span>
        </template>
      </div>
    </div>
    <div v-if="steps?.length" class="dots" aria-hidden="true">
      <span v-for="s in steps" :key="s.id" class="d" :class="`d-${s.status}`" />
    </div>
    <span class="time num caption">
      <ElapsedTimer v-if="active" :from="run.created_at" />
      <template v-else-if="run.ended_at">{{ humanDuration(run.ended_at - run.created_at) }}</template>
    </span>
    <ChevronRight class="chev" :size="16" />
  </RouterLink>
</template>

<style scoped>
.row {
  display: flex;
  align-items: center;
  gap: 16px;
  height: 100%;
  padding: 0 14px 0 16px;
  color: inherit;
  text-decoration: none;
  border-radius: var(--r-md);
  transition: background-color var(--t-fast);
}
.row:hover {
  background: var(--bg-hover);
}
.main {
  flex: 1;
  min-width: 0;
  display: grid;
  gap: 2px;
}
.l1 {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
}
.title-text {
  font-weight: 600;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.l2 {
  display: flex;
  gap: 6px;
  white-space: nowrap;
  overflow: hidden;
}
.cur {
  font-weight: 600;
}
.c-running {
  color: var(--st-running);
}
.c-waiting_resource {
  color: var(--st-waiting);
}
.c-awaiting_human {
  color: var(--st-human);
}
.c-failed {
  color: var(--st-fail);
}
.dots {
  display: flex;
  gap: 4px;
}
.d {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--bg-active);
  transition: background-color var(--t-base);
}
.d-running {
  background: var(--st-running);
  animation: soft-pulse 1.4s ease-in-out infinite;
}
.d-waiting_resource {
  background: var(--st-waiting);
}
.d-awaiting_human {
  background: var(--st-human);
}
.d-succeeded {
  background: var(--st-ok);
}
.d-cached {
  background: var(--st-cached);
}
.d-failed {
  background: var(--st-fail);
}
.time {
  min-width: 64px;
  text-align: right;
}
.chev {
  color: var(--text-3);
  flex: none;
}
</style>
