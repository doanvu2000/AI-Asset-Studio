<script setup lang="ts">
import { computed, useTemplateRef } from 'vue'
import { ChevronRight } from '@lucide/vue'
import type { RunRow, StepRow } from '@/api/types'
import { RUN_ACTIVE } from '@/api/types'
import { humanDuration, STEP_LABEL, stepName } from '@/composables/format'
import { recipeArt } from '@/assets/art'
import { rememberOrigin } from '@/composables/motion'
import { useRecipes } from '@/stores/recipes'
import { useRuns } from '@/stores/runs'
import RunStatusBadge from './RunStatusBadge.vue'
import ElapsedTimer from './ElapsedTimer.vue'
import RelTime from './RelTime.vue'
import BlobImage from './BlobImage.vue'

const props = defineProps<{ run: RunRow; steps?: StepRow[] }>()
const recipes = useRecipes()
const runs = useRuns()

const recipeName = computed(() => recipes.byId(props.run.recipe_id)?.name ?? (recipes.loaded ? 'Quy trình đã xoá' : ''))
const active = computed(() => RUN_ACTIVE.has(props.run.status))
const current = computed(() =>
  props.steps?.find((s) => ['running', 'waiting_resource', 'awaiting_human', 'failed'].includes(s.status))
)
const live = computed(() => (props.run.status === 'running' ? runs.livePreview(props.run.id) : null))
const hash = computed(() => runs.previewHash(props.run.id))
const art = computed(() => recipeArt(props.run.recipe_id))
const thumb = useTemplateRef<HTMLElement>('thumb')
</script>

<template>
  <RouterLink :to="`/runs/${run.id}`" class="row" @click="!live && hash && rememberOrigin(run.id, thumb)">
    <span ref="thumb" class="thumb checker" aria-hidden="true">
      <img v-if="live" class="live" :src="live" alt="" draggable="false" />
      <BlobImage v-else-if="hash" :hash="hash" :size="96" />
      <img v-else-if="art" class="art" :src="art" alt="" draggable="false" />
    </span>
    <div class="main">
      <div class="l1">
        <span class="title-text">{{ run.title || recipeName }}</span>
        <RunStatusBadge :status="run.status" />
      </div>
      <div class="l2 caption">
        <span class="recipe">{{ recipeName }}</span>
        <span>·</span>
        <RelTime :ts="run.created_at" />
        <template v-if="current">
          <span>·</span>
          <span class="cur" :class="`c-${current.status}`">{{ stepName(current) }}</span>
        </template>
      </div>
    </div>
    <div v-if="steps?.length" class="dots">
      <span
        v-for="s in steps"
        :key="s.id"
        class="d"
        :class="`d-${s.status}`"
        :title="`${stepName(s)}: ${STEP_LABEL[s.status]}`"
        role="img"
        :aria-label="`${stepName(s)}: ${STEP_LABEL[s.status]}`"
      />
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
  gap: 12px;
  height: 100%;
  padding: 0 14px 0 8px;
  color: inherit;
  text-decoration: none;
  border-radius: var(--r-md);
  transition: background-color var(--t-fast);
}
.row:hover {
  background: var(--bg-hover);
}
.thumb {
  position: relative;
  flex: none;
  width: 40px;
  height: 40px;
  border-radius: 8px;
  overflow: hidden;
  box-shadow: var(--hairline);
  background-size: 8px 8px;
  background-position: 0 0, 0 4px, 4px -4px, -4px 0;
}
.thumb :deep(.blob) {
  width: 100%;
  height: 100%;
}
.thumb img {
  width: 100%;
  height: 100%;
  object-fit: contain;
}
.thumb .art {
  padding: 3px;
  opacity: 0.9;
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
.recipe {
  overflow: hidden;
  text-overflow: ellipsis;
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
